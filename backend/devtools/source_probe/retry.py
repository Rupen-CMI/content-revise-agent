import json, calendar, time
from concurrent.futures import ThreadPoolExecutor
import feedparser, requests
res = json.load(open('results.json'))
now = time.time()
UAS = [
 "Feedly/1.0 (+http://www.feedly.com/fetcher.html; like FeedFetcher-Google)",
 "Mozilla/5.0 (compatible; Feedbin; +https://feedbin.com)",
 "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]
def retry(r):
    if r['ok'] or r.get('status') not in (403, 404, 405, None) and not r.get('why'):
        return r
    if r.get('status') == 404:   # genuinely wrong url, skip
        return r
    for ua in UAS:
        try:
            resp = requests.get(r['url'], headers={"User-Agent": ua, "Accept": "application/rss+xml,application/xml,text/xml,*/*"}, timeout=15)
            if resp.status_code != 200: continue
            d = feedparser.parse(resp.content)
            if not d.entries: continue
            ts = [calendar.timegm(e.get('published_parsed') or e.get('updated_parsed')) for e in d.entries if e.get('published_parsed') or e.get('updated_parsed')]
            r.update(ok=True, kind='feed', items=len(d.entries), ua=ua[:12], sample=(d.entries[0].get('title') or '')[:90])
            if ts: r['newest_days'] = round((now-max(ts))/86400,1); r['oldest_days']=round((now-min(ts))/86400,1)
            return r
        except Exception: continue
    return r
with ThreadPoolExecutor(16) as ex:
    out = list(ex.map(retry, res))
rec = [r for r,o in zip(res,out) if o['ok'] and not r.get('_was')]
json.dump(out, open('results2.json','w'), indent=1)
before = sum(1 for r in json.load(open('results.json')) if r['ok'])
print('before', before, 'after', sum(1 for r in out if r['ok']))
for r in out:
    if r.get('ua'): print('recovered:', r['name'], r.get('newest_days'))
