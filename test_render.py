import urllib.request, json
import urllib.error

tex_path = r'A:\Ashutosh Maurya\YT web D\Advance_\DinoMate\Latex-Compiler\Latex2Com\template\70+ ATS rating Resume Template\main.tex'
with open(tex_path, encoding='utf-8') as f:
    tex = f.read()

req = urllib.request.Request(
    'https://newlatexcompiler.onrender.com/compile', 
    data=json.dumps({'latex': tex, 'engine': 'pdflatex'}).encode('utf-8'), 
    headers={'Content-Type': 'application/json', 'X-Compiler-Secret': '1234556'}
)

try:
    r = urllib.request.urlopen(req, timeout=40)
    print("Success!", len(r.read()), "bytes")
except urllib.error.HTTPError as e:
    print(f"HTTP Error {e.code}")
    print(e.read().decode('utf-8', errors='replace'))
except Exception as e:
    print(e)
