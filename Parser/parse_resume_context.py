"""Explicit CLI for context-v2; baseline parser remains unchanged."""
import argparse,json
from pathlib import Path
from Parser.resume_parser import ResumeParseError
from Parser.resume_spacy_context import parse_resume_context

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('source',type=Path);parser.add_argument('--model',type=Path,required=True)
 parser.add_argument('--raw-skill-names',action='store_true',help='Disable resume-only normalization for comparison')
 parser.add_argument('--phrases-only',action='store_true');parser.add_argument('-o','--output',type=Path,required=True)
 args=parser.parse_args()
 if not args.source.exists():parser.error('Source does not exist')
 paths=sorted(p for p in args.source.rglob('*') if p.is_file() and p.suffix.lower() in {'.pdf','.txt'}) if args.source.is_dir() else [args.source]
 if any(p.resolve()==args.output.resolve() for p in paths):parser.error('Output must not overwrite input')
 results=[];errors=[]
 for index,path in enumerate(paths,1):
  try:
   parsed=parse_resume_context(path.name,path.read_bytes(),args.model,allow_novel=not args.phrases_only,normalize=not args.raw_skill_names)
   results.append({'file_number':index,**parsed.to_anonymized_dict()})
  except (ResumeParseError,OSError) as exc:errors.append({'file_number':index,'error':str(exc)})
 args.output.write_text(json.dumps({'parsed':len(results),'failed':len(errors),'results':results,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
 return 1 if errors else 0
if __name__=='__main__':raise SystemExit(main())
