"""Integrate Analytics as a first-class GBR player view at build time.

Patches only generated _site HTML, never the source template. Idempotent.
"""
from pathlib import Path
import sys

STYLE = '''<style id="gbr-observatory-style">
#gbrObservatory{position:fixed;z-index:2100;inset:0;display:none;background:#0a0c09}
body.gbr-observatory-open #gbrObservatory{display:block}
#gbrObservatory iframe{display:block;width:100%;height:100%;border:0;background:#0b0d0a}
body.gbr-observatory-open #playingCoverViewport{visibility:hidden!important}
</style>'''
VIEW = '''<div id="gbrObservatory" aria-label="GBR Signal Observatory" aria-hidden="true"><iframe title="GBR Signal Observatory" loading="lazy" data-src="analytics/"></iframe></div>
<script id="gbr-observatory-bridge">
(()=>{const b=document.getElementById('gbrAnalyticsButton'),v=document.getElementById('gbrObservatory');if(!b||!v)return;
const frame=v.querySelector('iframe');function show(){if(!frame.src)frame.src=frame.dataset.src;document.body.classList.add('gbr-observatory-open');v.setAttribute('aria-hidden','false');b.classList.add('active');b.textContent='Music';}
function hide(){document.body.classList.remove('gbr-observatory-open');v.setAttribute('aria-hidden','true');b.classList.remove('active');b.textContent='Analytics';}
b.addEventListener('click',()=>document.body.classList.contains('gbr-observatory-open')?hide():show());
window.addEventListener('message',e=>{if(e.origin===location.origin&&e.source===frame.contentWindow&&e.data?.type==='gbr:close-observatory')hide()});
document.getElementById('knowledgeButton')?.addEventListener('click',hide);
document.addEventListener('keydown',e=>{if(document.body.classList.contains('gbr-observatory-open')&&e.key==='Escape'){hide();e.stopImmediatePropagation()}},true);
if(location.hash==='#analytics')show();
})();
</script>'''
for filename in sys.argv[1:]:
    path=Path(filename)
    html=path.read_text(encoding='utf-8')
    if 'id="gbrObservatory"' in html:
        print('Analytics view already integrated:',path)
        continue
    anchor='<button class="utility-button" id="knowledgeButton" type="button">Knowledge</button>'
    if anchor not in html or '</head>' not in html or '</body>' not in html:
        raise SystemExit(f'GBR player structure not recognised in {path}')
    html=html.replace(anchor,anchor+'\n      <button class="utility-button" id="gbrAnalyticsButton" type="button">Analytics</button>',1)
    html=html.replace('</head>',STYLE+'\n</head>',1)
    html=html.replace('</body>',VIEW+'\n</body>',1)
    path.write_text(html,encoding='utf-8')
    print('Integrated analytics workspace into:',path)
