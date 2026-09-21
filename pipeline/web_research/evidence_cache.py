"""Reuse recent public evidence snapshots within an artifact-only campaign."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path


def recent_pages(root, now=None, max_age_hours=4):
    now=now or datetime.now(timezone.utc)
    cutoff=now-timedelta(hours=max_age_hours)
    pages={}
    for file in sorted(Path(root).rglob('evidence.json')):
        manifest=file.parent.parent/'run-manifest.json'
        if not manifest.exists():continue
        meta=json.loads(manifest.read_text())
        for url,page in json.loads(file.read_text()).items():
            if not page.get('text') or page.get('error'):continue
            stamp=page.get('fetched_at') or meta.get('started_at')
            try: fetched=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            except (AttributeError,ValueError):continue
            if fetched.tzinfo is None or not cutoff<=fetched<=now:continue
            previous=pages.get(url)
            if previous and previous['fetched_at']>=fetched.isoformat():continue
            pages[url]={**page,'fetched_at':fetched.isoformat(),'cache_source_run':page.get('cache_source_run') or meta.get('run_id'),'cache_hit':True}
    return pages
