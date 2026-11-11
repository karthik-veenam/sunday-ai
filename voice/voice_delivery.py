"""Bounded voice-only cues. Identical stdlib contract installed in brain and TTS."""
import re
TAGS=frozenset(('happy','excited','curious','laughs','chuckles','sighs','exhales','whispers',
               'sarcastic','mischievously','laughs harder','starts laughing','wheezing','crying',
               'snorts','frustrated sigh','happy gasp','sad','calm','warm','thoughtful','content','playful','softly','peaceful'))
ALIASES={'laughing':'laughs','whispering':'whispers','sigh':'sighs'}
TAG_RE=re.compile(r'\[[a-zA-Z][a-zA-Z\s]{0,40}\]')
SCHEMA={'type':'object','properties':{'tags':{'type':'array','items':{'type':'string','enum':sorted(TAGS)},'maxItems':2,'uniqueItems':True}},'required':['tags'],'additionalProperties':False,
        'description':'Optional voice delivery for this reply, separate from face. Pick up to two appropriate ElevenLabs audio tags in this same inference. Usually one tone or brief reaction, or [] for neutral. Do not add words or an extra model call.'}

def normalize(tag):
    tag=' '.join(tag.strip().lower().split())
    return ALIASES.get(tag,tag)

def plain(text):return TAG_RE.sub('',text).strip()

def validate(value):
    if not isinstance(value,dict) or set(value)!={'tags'}:return None
    tags=value['tags']
    if not isinstance(tags,list) or len(tags)>2 or any(not isinstance(t,str) for t in tags):return None
    tags=[normalize(t) for t in tags]
    if any(t not in TAGS for t in tags) or len(set(tags))!=len(tags):return None
    return tags

def tags_in(text):
    return [normalize(m[0][1:-1]) for m in TAG_RE.finditer(text) if normalize(m[0][1:-1]) in TAGS]

def render(text,directives=(),budget=2):
    """Keep authored inline placement; structured cues prefix once. Never produce sound-only text."""
    if not plain(text):return ''
    chosen=[]
    for tag in directives:
        tag=normalize(tag)
        if tag in TAGS and tag not in chosen and len(chosen)<max(0,budget):chosen.append(tag)
    prefix=''.join('['+tag+'] ' for tag in chosen)
    def replace(match):
        tag=normalize(match[0][1:-1])
        if tag not in TAGS or tag in chosen or len(chosen)>=max(0,budget):return ''
        chosen.append(tag);return '['+tag+']'
    return (prefix+TAG_RE.sub(replace,text)).strip()

def delivery(text,enabled=True):
    return render(text) if enabled else plain(text)
