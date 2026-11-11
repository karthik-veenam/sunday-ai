"""Plain Telegram text without parse-mode errors or leaked markup delimiters."""
import re

def telegram_text(text):
    code=[]
    def protect(match):
        code.append(match.group(1));return '\ue000'+str(len(code)-1)+'\ue001'
    text=re.sub(r'```(?:[\w+-]+[ \t]*\n)?(.*?)```',protect,text,flags=re.S)
    text=re.sub(r'`([^`\n]+)`',protect,text)
    for marker in ('**','__'):
        pattern=r'(?<!\w)'+re.escape(marker)+r'(?=\S)(.+?)(?<=\S)'+re.escape(marker)+r'(?!\w)'
        text=re.sub(pattern,r'\1',text,flags=re.S)
    text=re.sub(r'(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\w)',r'\1',text)
    text=re.sub(r'^#{1,6}\s+','',text,flags=re.M)
    text=re.sub(r'\[([^]\n]+)\]\((https?://[^)\s]+)\)',r'\1 (\2)',text)
    text=re.sub('\ue000([0-9]+)\ue001',lambda m:code[int(m.group(1))],text)
    return text.strip()
