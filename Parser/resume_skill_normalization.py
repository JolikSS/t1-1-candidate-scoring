"""Versioned resume-only aliases. Does not change shared scorer/evaluation keys."""
import unicodedata
from scorer.skill_matching import canonical_skill_key
VERSION='resume-normalization-1.0'
_ALIASES={
 'excel':'Excel','ms excel':'Excel','microsoft excel':'Excel',
 'powerpoint':'PowerPoint','ms powerpoint':'PowerPoint','microsoft powerpoint':'PowerPoint',
 'поиск информации в интернет':'Поиск информации в интернете',
 'поиск информации в интернете':'Поиск информации в интернете',
}
def normalize_skill(name):
 literal=' '.join(unicodedata.normalize('NFC',name).split())
 return _ALIASES.get(literal.casefold(),literal)
def resume_skill_key(name):
 return canonical_skill_key(normalize_skill(name))
def normalize_result(skills,evidence):
 names={};changes=[]
 for skill in skills:
  normalized=normalize_skill(skill);key=resume_skill_key(normalized)
  names.setdefault(key,normalized)
  if normalized!=skill:changes.append({'input':skill,'output':normalized})
 normalized_evidence=[]
 for item in evidence:
  changed=dict(item);value=normalize_skill(item['skill']);changed['skill']=names.get(resume_skill_key(value),value)
  if changed['skill']!=item['skill']:changed['source_skill']=item['skill']
  normalized_evidence.append(changed)
 return list(names.values()),normalized_evidence,changes
