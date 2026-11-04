"""Compare ordinary imported function bytecode with independently compiled pinned source."""
import hashlib,importlib,json,struct,types
from pathlib import Path

def canonical_code(code):
 def constant(x):
  if isinstance(x,types.CodeType):return canonical_code(x)
  if isinstance(x,(tuple,list)):return[constant(a)for a in x]
  if isinstance(x,frozenset):return{'frozenset':sorted((constant(a)for a in x),key=lambda a:json.dumps(a,sort_keys=True))}
  if isinstance(x,(str,int,float,bool,type(None))):return x
  return{'type':type(x).__name__,'repr':repr(x)}
 return dict(code=code.co_code.hex(),consts=[constant(c)for c in code.co_consts],names=code.co_names,varnames=code.co_varnames,freevars=code.co_freevars,cellvars=code.co_cellvars,argcount=code.co_argcount,posonlyargcount=code.co_posonlyargcount,kwonlyargcount=code.co_kwonlyargcount,flags=code.co_flags,stacksize=code.co_stacksize,exceptiontable=code.co_exceptiontable.hex())
def fingerprint(code):return hashlib.sha256(json.dumps(canonical_code(code),sort_keys=True,separators=(',',':')).encode()).hexdigest()
def find(code,path):
 if not path:return code
 matches=[c for c in code.co_consts if isinstance(c,types.CodeType)and c.co_name==path[0]]
 assert len(matches)==1,(path,len(matches));return find(matches[0],path[1:])
def audit_functions():
 targets={'llm.securing.mask_filter':['SecuringEngine._legal','SecuringEngine.copy','SecuringEngine._final_action_result'],'stowage.solver.securing_screen':['DeckSecuringScreen._evaluate_component','DeckSecuringScreen._evaluate','NecessaryDeckSecuringScreen.__init__'],'stowage.checker.lashing':['_check_lashing','_load_graph','_chains','_end_response']};result={}
 for name,functions in targets.items():
  m=importlib.import_module(name);p=Path(m.__file__);source=p.read_bytes();compiled=compile(source,str(p),'exec',dont_inherit=True,optimize=0);stat=p.stat();cached=Path(m.__cached__)if getattr(m,'__cached__',None)else None;header=cached.read_bytes()[:16]if cached and cached.exists()else None
  entries=[]
  for path in functions:
   obj=m
   for component in path.split('.'):obj=getattr(obj,component)
   actual=obj.__code__;expected=find(compiled,path.split('.'));entries.append(dict(function=path,loaded_fingerprint=fingerprint(actual),compiled_source_fingerprint=fingerprint(expected),matches_source=fingerprint(actual)==fingerprint(expected),loaded_co_filename=actual.co_filename,loaded_firstlineno=actual.co_firstlineno,source_firstlineno=expected.co_firstlineno))
  result[name]=dict(source_path=str(p),source_sha256=hashlib.sha256(source).hexdigest(),source_mtime_ns=stat.st_mtime_ns,source_size=stat.st_size,source_inode=stat.st_ino,pyc_path=str(cached)if cached else None,pyc_header_hex=header.hex()if header else None,pyc_flags=struct.unpack('<I',header[4:8])[0]if header else None,pyc_timestamp=struct.unpack('<I',header[8:12])[0]if header and struct.unpack('<I',header[4:8])[0]==0 else None,pyc_source_size=struct.unpack('<I',header[12:16])[0]if header and struct.unpack('<I',header[4:8])[0]==0 else None,functions=entries)
 return result
