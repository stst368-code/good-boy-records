"""Integrate the editable process guide without routing into the legacy map."""
from pathlib import Path
import sys

STYLE = '''<style id="gbr-making-style">
#gbrMaking{position:fixed;z-index:2200;inset:0;display:none;background:#0b100e}
body.gbr-making-open #gbrMaking{display:block}
#gbrMaking iframe{display:block;width:100%;height:100%;border:0;background:#0b100e}
body.gbr-making-open #playingCoverViewport{visibility:hidden!important}
@media(max-width:900px){#knowledgeButton{white-space:normal!important;line-height:1.1!important}}
</style>'''
VIEW = '''<section id="gbrMaking" aria-label="How it’s made" aria-hidden="true"><iframe title="How it’s made" loading="lazy" data-src="how-its-made/"></iframe></section>
<script id="gbr-making-bridge">
(()=>{const button=document.getElementById('knowledgeButton'),view=document.getElementById('gbrMaking');if(!button||!view)return;
const frame=view.querySelector('iframe'),analytics=document.getElementById('gbrAnalyticsButton');
function hide(){document.body.classList.remove('gbr-making-open');view.setAttribute('aria-hidden','true');button.classList.remove('active');if(frame.contentDocument)frame.contentDocument.querySelectorAll('audio,video').forEach(p=>p.pause());button.focus();}
function show(){if(document.body.classList.contains('gbr-observatory-open'))analytics?.click();document.querySelectorAll('audio,video').forEach(p=>p.pause());if(!frame.src)frame.src=frame.dataset.src;document.body.classList.add('gbr-making-open');view.setAttribute('aria-hidden','false');button.classList.add('active');}
button.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();document.body.classList.contains('gbr-making-open')?hide():show();},true);
window.addEventListener('message',e=>{if(e.origin!==location.origin||e.source!==frame.contentWindow)return;if(e.data?.type==='gbr:close-making')hide();else if(e.data?.type==='gbr:open-analytics'){hide();analytics?.click();}});
document.addEventListener('keydown',e=>{if(e.ctrlKey||e.metaKey||e.altKey||e.target.closest('input,textarea,select,[contenteditable=true]'))return;if(e.key.toLowerCase()==='k'){e.preventDefault();e.stopImmediatePropagation();document.body.classList.contains('gbr-making-open')?hide():show();}else if(e.key==='Escape'&&document.body.classList.contains('gbr-making-open')){e.stopImmediatePropagation();hide();}},true);
})();
</script>'''

for filename in sys.argv[1:]:
    path = Path(filename)
    html = path.read_text(encoding='utf-8')
    if 'id="gbrMaking"' in html:
        continue
    if 'id="knowledgeButton"' not in html:
        raise ValueError('Player is missing How it’s made navigation')
    html = html.replace('</head>', STYLE+'\n</head>', 1).replace('</body>', VIEW+'\n</body>', 1)
    path.write_text(html, encoding='utf-8')
