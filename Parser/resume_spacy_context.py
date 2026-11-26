"""Context pilot v2: line inference, calibrated beam scores, bounded novel terms."""
import json,re
from pathlib import Path
from functools import lru_cache
from dataclasses import replace
from collections import defaultdict
from Parser.resume_parser import parse_resume_bytes,default_resume_skill_catalog
from Parser.resume_skill_matching import resume_skill_mentions
from Parser.document_structure import title_candidate,COMPANY
from scorer.skill_matching import canonical_skill_key
from Parser.resume_skill_normalization import normalize_result,VERSION as NORMALIZATION_VERSION

def _ner_candidates(nlp,text):
 """Score skill spans at inference time without experiment/training imports."""
 doc=nlp.make_doc(text);ner=nlp.get_pipe('ner')
 beams=ner.beam_parse([doc],beam_width=16,beam_density=0.0001)
 scores=defaultdict(float)
 for score,entities in ner.moves.get_beam_parses(beams[0]):
  for a,b,label in entities:
   if label=='SKILL':scores[(doc[a:b].start_char,doc[a:b].end_char)]+=score
 return scores

@lru_cache(maxsize=2)
def _load(path):
 import spacy
 from spacy.matcher import PhraseMatcher
 root=Path(path);nlp=spacy.load(root/'ner');phrases=json.loads((root/'phrases.json').read_text())
 matcher=PhraseMatcher(nlp.vocab,attr='LOWER');matcher.add('SKILL',[nlp.make_doc(p) for p in phrases])
 known={canonical_skill_key(p) for p in [*phrases,*default_resume_skill_catalog()]}
 return nlp,matcher,known

_PERFORMED = re.compile(r'(?i)\b(?:работа(?:л[аи]?|ю|ем)\s+(?:с|со)|использ(?:овал[аи]?|ую|уем)|using|used\s+|worked\s+with)\b')
_BUILT = re.compile(r'(?i)\b(?:разработал[аи]?|разрабатывал[аи]?|реализовал[аи]?|спроектировал[аи]?|переписал[аи]?|внедрил[аи]?|настроил[аи]?|built|developed|implemented)\b')
_TECH_OBJECT = re.compile(r'(?i)\b(?:пайплайн\w*|сервис\w*|приложен\w*|сайт\w*|интеграц\w*|систем\w*|решени\w*|на|using|with)\b')
_ORG = re.compile(r'(?i)\b(?:ООО|OOO|ОАО|ЗАО|ПАО|АО|ИП|LLC|Ltd|Company|Corporation|Corp|Bank)\b')
_RUS_TECH = re.compile(r'(?i)администрирован|автоматизац|контейнеризац|виртуализац|тестирован|программирован|проектирован|микросервис|баз[аы]\s+данных|сетев|безопасност|анализ\w*\s+данных')

def professional_context(line,start,known):
 """Require a bounded, affirmative work cue before an unknown candidate."""
 prefix=re.split(r'[;!?]',line[:start])[-1][-120:]
 if _ORG.search(prefix):return False
 if _PERFORMED.search(prefix):return True
 if _BUILT.search(prefix) and _TECH_OBJECT.search(prefix):return True
 if re.search(r'(?i)\b(?:стек|технологии|инструменты|библиотек\w*|фреймворк\w*)\s*:?\s*$',prefix):return True
 # A named variant after a known technology, e.g. Linux (Example Linux).
 if prefix.rstrip().endswith('('):
  words=prefix.rstrip()[:-1].split()
  if any(canonical_skill_key(' '.join(words[-n:])) in known for n in range(1,min(3,len(words))+1)):return True
 return False

def allowed_novel(name,line,section,known,*,start=None,confidence=1.0):
 if canonical_skill_key(name) in known:return True
 single_ascii=bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9.+#_-]{1,39}',name))
 if single_ascii and (section=='skills' or re.search(r'(?i)(?:стек|технологии|инструмент|библиотек|фреймворк|с помощью|на базе|using|stack)\b',line)):return True
 if start is None:start=line.casefold().find(name.casefold())
 if start<0 or not professional_context(line,start,known):return False
 if _ORG.search(name) or title_candidate(name):return False
 if single_ascii:return True
 words=name.split()
 if not 1<=len(words)<=4 or len(name)>80 or not re.fullmatch(r'[\w.+#/-]+(?: [\w.+#/-]+){0,3}',name):return False
 if re.search(r'[А-Яа-яЁё]',name):return confidence>=.85 and bool(_RUS_TECH.search(name))
 # Unknown multiword names need a technical anchor, not merely a person's name.
 anchors=[w for w in words if canonical_skill_key(w) in known]
 acronym=any(re.fullmatch(r'[A-Z]{2,6}',w) for w in words)
 head=bool(re.search(r'(?i)\b(?:SDK|API|CLI|DB|Engine|Server|Framework|Cloud|Cluster|Studio|Protocol|Library|Kit|Tools?|Office)\b',name))
 if confidence<.75 or not (anchors or acronym or head):return False
 # Do not merge a flat list of two already known technologies into a new skill.
 matches=resume_skill_mentions(name,list(default_resume_skill_catalog())+list(known))
 return len({m.canonical for m in matches})<2

def role_like_skill_context(line,known):
 """Allow known phrases only in a demonstrated list or explicit action context."""
 if COMPANY.match(line.strip()):return False
 if re.search(r'(?i)\bс\s+(?:(?:практическим|коммерческим)\s+)?опытом\b',line):return True
 if re.search(r"(?i)\b(?:переписал[аи]?|использова[лт]\w*|разработа[лт]\w*|реализова[лт]\w*|стек|технологии)\b",line):return True
 parts=[part.strip() for part in re.split(r"\s+/\s+|[;,]",line.strip()) if part.strip()]
 return len(parts)>=2 and all(canonical_skill_key(part) in known for part in parts)

def parse_resume_context(filename,content,model,*,threshold=None,allow_novel=True,trace=None,normalize=True):
 if threshold is None:
  chosen=json.loads((Path(model)/'context-policy.json').read_text())['chosen']
  if chosen is None:raise ValueError('No development-approved NER threshold')
  threshold=chosen['threshold']
 if not 0<=threshold<=1:raise ValueError('Threshold must be between 0 and 1')
 parsed=parse_resume_bytes(filename,content,include_unlisted_skills=True);nlp,matcher,known=_load(str(Path(model).resolve()))
 names={canonical_skill_key(s):s for s in parsed.skills};evidence=list(parsed.parse_details['skill_evidence'])
 def record(section,start,end,name,origin,score,decision,canonical=None):
  if trace is not None:trace.append({'section':section,'start':start,'end':end,'text':name,'canonical':canonical if canonical is not None else canonical_skill_key(name),'origin':origin,'score':score,'decision':decision})
 if trace is not None:
  for skill in parsed.skills:record(None,None,None,skill,'baseline',None,'accepted_baseline')
 if trace is not None:
  from scorer.professional_expressions import extract_skill_mentions
  catalog=list(default_resume_skill_catalog())
  for source_section,value in parsed.parse_details['sections'].items():
   positive_catalog={(m.canonical,m.start,m.end) for m in resume_skill_mentions(value,catalog)}
   for mention in extract_skill_mentions(value,catalog):
    decision='accepted_catalog' if (mention.canonical,mention.start,mention.end) in positive_catalog else 'negative_or_boundary'
    record(source_section,mention.start,mention.end,mention.text,'catalog',None,decision,canonical=mention.canonical)
 # Complete the phrase-only pass first. Rejected or overlapping NER proposals
 # must never suppress a phrase or replace its previously selected display name.
 for origin in (['phrase','ner'] if allow_novel else ['phrase']):
  for section,text in parsed.parse_details['sections'].items():
   section_blocked=section in {'title','education','unclassified'}
   if section_blocked and trace is None:continue
   offset=0
   for line in text.splitlines(keepends=True):
    blocked='excluded_section' if section_blocked else ('metadata_line' if re.search(r'(?i)Резюме|Обновлен[оа]|https?://|@',line) else ('role_or_company_line' if section!='skills' and (title_candidate(line.strip()) or COMPANY.match(line.strip())) else None))
    overridden=blocked=='role_or_company_line' and role_like_skill_context(line,known)
    original_gate=blocked
    if overridden:blocked=None
    if blocked and trace is None:
     offset+=len(line);continue
    if origin=='phrase':
     doc=nlp.make_doc(line);spans={(doc[a:b].start_char,doc[a:b].end_char,1.0) for _,a,b in matcher(doc)}
    else:spans={(a,b,score) for (a,b),score in _ner_candidates(nlp,line).items() if score>=threshold or trace is not None}
    eligible=[]
    for a,b,score in spans:
     name=line[a:b].strip();key=canonical_skill_key(name)
     permitted=origin=='phrase' or allowed_novel(name,line,section,known,start=a,confidence=score)
     gate=original_gate if overridden and (key not in known and not (origin=='ner' and permitted)) else blocked
     if not gate and score>=threshold and permitted and name and len(name)<=100:eligible.append(name)
    positive={(m.canonical,m.start,m.end) for m in resume_skill_mentions(line,list(dict.fromkeys(eligible)))}
    for a,b,score in sorted(spans):
     name=line[a:b].strip();key=canonical_skill_key(name)
     permitted=origin=='phrase' or allowed_novel(name,line,section,known,start=a,confidence=score)
     candidate_gate=original_gate if overridden and (key not in known and not (origin=='ner' and permitted)) else blocked
     decision=(candidate_gate or ('below_threshold' if origin=='ner' and score<threshold else
               'empty_candidate' if not name else 'already_present' if key in names else
               'too_long' if len(name)>100 else 'novel_shape_or_context' if not permitted else
               'negative_or_boundary' if (key,a,b) not in positive else 'accepted'))
     record(section,offset+a,offset+b,name,origin,score,decision)
     if trace is not None and overridden:trace[-1]['context_override']=candidate_gate is None
     if decision!='accepted':continue
     names[key]=name;evidence.append({'skill':name,'section':section,'text':name,'start':offset+a,'end':offset+b,'extractor':'spacy-context-v3.1-'+origin,'confidence':score})
    offset+=len(line)
 raw_skills=list(names.values())
 skills,normalized_evidence,changes=normalize_result(raw_skills,evidence) if normalize else (raw_skills,evidence,[])
 details={**parsed.parse_details,'skill_evidence':normalized_evidence,'spacy_model':'context-v3','spacy_adapter_version':'context-v3.1','spacy_threshold':threshold,
          'skills_before_normalization':raw_skills,'skill_normalization_version':NORMALIZATION_VERSION if normalize else None,
          'skill_normalization_changes':changes}
 return replace(parsed,skills=skills,parse_details=details)
