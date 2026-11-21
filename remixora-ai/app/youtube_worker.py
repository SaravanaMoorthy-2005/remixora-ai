"""Runs only in a bounded subprocess. No shell commands, cookies or arbitrary URLs."""
import json
import logging
import math
import os
import shutil
import sys
from pathlib import Path
from .youtube import canonical_youtube_url,json3_segments,safe_download_error

logger=logging.getLogger('remixora.youtube')

def run(url,folder,language):
    from yt_dlp import YoutubeDL
    from .media import probe
    url=canonical_youtube_url(url);folder=Path(folder)
    limit=int(os.getenv('MAX_UPLOAD_MB','150'))*1024**2

    collected_errors=[]
    class WorkerLogger:
        def debug(self,*args):pass
        def warning(self,msg):
            text=str(msg).strip()
            if any(k in text.lower() for k in ['warning','error','failed','blocked','skip','proxy','403','429','sign in']):
                logger.warning("[yt-dlp] %s", text)
        def error(self,msg):
            text=str(msg).strip()
            logger.error("[yt-dlp] %s", text)
            collected_errors.append(text)

    def progress(info):
        if info.get('downloaded_bytes',0)>limit:raise ValueError('size limit')
        total=sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
        if total>limit*2.1:raise ValueError('size limit')

    ffmpeg_path=shutil.which('ffmpeg')
    node_path=shutil.which('node') or shutil.which('nodejs')
    socket_timeout=int(os.getenv('YTDLP_SOCKET_TIMEOUT','30'))
    retries=int(os.getenv('YTDLP_RETRIES','5'))

    opts={'quiet':True,'logger':WorkerLogger(),'noplaylist':True,'cachedir':False,
          'source_address':'0.0.0.0','socket_timeout':socket_timeout,'retries':retries,
          'fragment_retries':retries,'file_access_retries':3,'allowed_extractors':['youtube'],
          'restrictfilenames':True,'max_filesize':limit,'concurrent_fragment_downloads':1,
          'progress_hooks':[progress],'outtmpl':str(folder/'source.%(ext)s'),
          'overwrites':True,'continuedl':False}

    if ffmpeg_path:
        opts['ffmpeg_location']=ffmpeg_path
        opts['format']='bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]/best[height<=720]/best'
        opts['merge_output_format']='mp4'
    else:
        opts['format']='b[height<=720][ext=mp4]/b[height<=720]/best[height<=720]/best'

    if node_path:
        opts['js_runtimes']={'node':{'path':node_path}}

    proxy=(os.getenv('YTDLP_PROXY') or os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY') or '').strip()
    if proxy:
        opts['proxy']=proxy
        safe_proxy=proxy.split('@')[-1] if '@' in proxy else 'configured'
        logger.info("[YouTube import worker] YTDLP_PROXY active: %s", safe_proxy)

    custom_clients=os.getenv('YTDLP_PLAYER_CLIENTS')
    if custom_clients:
        clients=[c.strip() for c in custom_clients.split(',') if c.strip()]
    else:
        clients=['default','android','mweb']
    opts['extractor_args']={'youtube':{'player_client':clients}}

    with YoutubeDL(opts) as ydl:
        info=ydl.extract_info(url,download=False)
        duration=float(info.get('duration') or 0)
        if info.get('is_live') or info.get('live_status')=='is_upcoming' or not math.isfinite(duration) or not 0<duration<=int(os.getenv('MAX_DURATION_SECONDS','1800')):raise ValueError('duration limit')
        if info.get('has_drm'):raise ValueError('unavailable protected media')
        if info.get('filesize',0) and info['filesize']>limit:raise ValueError('size limit')
        downloaded=ydl.extract_info(url,download=True)
    media=next((p for p in folder.glob('source.*') if p.suffix in {'.mp4','.webm','.mkv'}),None)
    if not media or media.stat().st_size>limit:raise ValueError('size limit or no playable download')
    actual,kind=probe(media)
    captions=[];provenance='none';caption_language=None
    manual=info.get('subtitles') or {};automatic=info.get('automatic_captions') or {}
    for catalog,is_auto in [(manual,False),(automatic,True)]:
        choices=[k for k in catalog if k==language or k.startswith(language+'-')]
        if choices:
            caption_language=choices[0];provenance='youtube_auto_captions' if is_auto else 'youtube_captions';break
    if caption_language:
        try:
            subopts={**opts,'skip_download':True,'writesubtitles':provenance=='youtube_captions','writeautomaticsub':provenance=='youtube_auto_captions','subtitleslangs':[caption_language],'subtitlesformat':'json3','ignoreerrors':False}
            with YoutubeDL(subopts) as ydl:ydl.extract_info(url,download=True)
            subtitle=next(folder.glob('*.json3'),None)
            if subtitle and subtitle.stat().st_size<=2_000_000:captions=json3_segments(json.loads(subtitle.read_text(encoding='utf-8')),actual)
        except Exception as sub_ex:
            logger.warning("[YouTube import worker] Subtitle extraction skipped: %s", sub_ex)
            captions=[]
    result={'path':str(media.resolve()),'title':str(info.get('title') or 'YouTube source')[:200],'duration':actual,'kind':kind,'segments':captions,'transcript_source':provenance if captions else 'none','url':url}
    (folder/'result.json').write_text(json.dumps(result),encoding='utf-8')

if __name__=='__main__':
    try:run(*sys.argv[1:4])
    except Exception as ex:
        err_text=' '.join([str(ex)] + getattr(sys.modules[__name__], '_collected_errors', []))
        logger.error("[YouTube import worker failure] %s", ex, exc_info=True)
        (Path(sys.argv[2])/'error.json').write_text(json.dumps({'error':safe_download_error(ex)}),encoding='utf-8')
        sys.exit(1)
