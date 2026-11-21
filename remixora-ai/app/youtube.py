"""Strict YouTube URL ingestion, with an isolated and time-bounded downloader."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit,parse_qs

def canonical_youtube_url(value):
    try:
        value=value.strip()
        if value.startswith(('youtube.com/','www.youtube.com/','m.youtube.com/','youtu.be/','www.youtu.be/')):value='https://'+value
        url=urlsplit(value)
        if url.scheme!='https' or url.username or url.password or url.port not in {None,443}:raise ValueError()
        host=(url.hostname or '').lower()
        if host in {'youtu.be','www.youtu.be'}:video_id=url.path.strip('/')
        elif host in {'youtube.com','www.youtube.com','m.youtube.com'}:
            if url.path=='/watch':video_id=parse_qs(url.query).get('v',[''])[0]
            else:
                match=re.fullmatch(r'/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?',url.path)
                video_id=match[1] if match else ''
        else:raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):raise ValueError()
    except ValueError:raise ValueError('Paste an HTTPS YouTube watch, Shorts or youtu.be link to a single video.')
    return 'https://www.youtube.com/watch?v='+video_id

def json3_segments(data,duration):
    """Normalize rolling automatic captions into ordered, non-overlapping cues."""
    cues=[];previous=''
    for event in data.get('events',[]):
        text=' '.join(''.join(s.get('utf8','') for s in event.get('segs',[])).split())
        if not text or not event.get('dDurationMs'):continue
        start=max(0,float(event.get('tStartMs',0))/1000)
        end=min(duration,start+float(event['dDurationMs'])/1000)
        if text==previous:continue
        original=text
        if previous and text.startswith(previous+' '):text=text[len(previous):].strip()
        previous=original
        if start<end:cues.append({'start':start,'end':end,'text':text})
    cues.sort(key=lambda s:s['start'])
    cleaned=[]
    for i,cue in enumerate(cues):
        if i+1<len(cues):cue['end']=min(cue['end'],cues[i+1]['start'])
        if cue['end']>cue['start']:cleaned.append(cue)
    return cleaned

def download_youtube(url,folder,language='en'):
    import logging
    logger=logging.getLogger('remixora.youtube')
    url=canonical_youtube_url(url);folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    timeout_sec=int(os.getenv('YTDLP_TIMEOUT_SECONDS','600'))
    try:
        result=subprocess.run([sys.executable,'-m','app.youtube_worker',url,str(folder),language],capture_output=True,text=True,timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        logger.error("[YouTube import] Worker process timed out after %ds for %s", timeout_sec, url)
        raise ValueError('YouTube import timed out. Try again or upload your original file.')
    if result.returncode or not (folder/'result.json').is_file():
        err_msg=''
        if (folder/'error.json').is_file():
            try:
                err_data=json.loads((folder/'error.json').read_text(encoding='utf-8'))
                err_msg=err_data.get('error','')
            except Exception:pass
        if result.stderr:
            logger.error("[YouTube import] Worker stderr: %s", result.stderr.strip())
        if not err_msg and result.stderr:
            err_msg=safe_download_error(result.stderr)
        if not err_msg:
            err_msg='YouTube import could not run. Check yt-dlp, Node.js, FFmpeg and your connection in System Health.'
        raise ValueError(err_msg)
    return json.loads((folder/'result.json').read_text(encoding='utf-8'))

def safe_download_error(error):
    message=str(error).lower()
    has_proxy=bool((os.getenv('YTDLP_PROXY') or os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY') or '').strip())
    if 'duration limit' in message:
        return 'This video exceeds the configured duration limit. Upload a shorter source or adjust MAX_DURATION_SECONDS.'
    if 'size limit' in message or 'filesize' in message:
        return 'This download exceeds the configured media size limit. Upload a smaller source or adjust MAX_UPLOAD_MB.'
    if any(p in message for p in ['proxy', 'tunnel', 'socks', 'cannot connect to proxy', 'proxyerror']):
        return 'Proxy connection failed while contacting YouTube. Check your YTDLP_PROXY URL or upload your original file.'
    if any(x in message for x in ['private', 'unavailable']) and not any(k in message for k in ['bot', 'sign in']):
        return 'This YouTube video is private or unavailable. Use a publicly accessible video you may repurpose, or upload your original file.'
    if any(x in message for x in ['age', 'members', 'premium']):
        return 'This YouTube video requires account sign-in (age or member restricted). Upload your original file.'
    if any(x in message for x in ['sign in', 'bot', '403', '429', 'captcha', 'challenge']):
        if has_proxy:
            return 'YouTube restricted this video or proxy IP. Verify your YTDLP_PROXY setting, or upload your original file.'
        return 'YouTube restricted this server IP (bot check/403). Configure YTDLP_PROXY on Railway or upload your original file. No cookies or access bypass are used.'
    if 'timeout' in message or 'timed out' in message:
        return 'YouTube connection timed out. Check network or set YTDLP_PROXY on Railway, or upload your original file.'
    if 'ffmpeg' in message:
        return 'FFmpeg is required to process this video format. Verify server FFmpeg or upload your original file.'
    return 'YouTube could not provide this media. Check connectivity, configure YTDLP_PROXY on Railway, or upload your original file.'
