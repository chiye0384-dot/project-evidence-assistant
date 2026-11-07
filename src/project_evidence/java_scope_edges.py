"""Pure lexical evidence view; never a Java resolver or an answer/citation editor.

Only complete try chains and unique, same-class, arity-matching unqualified
call candidates are reported. Unsupported/incomplete constructs are explicit.
No I/O, execution, repository lookup, model use, selection, or response repair.
"""
import json
import re

FORMAT='java_scope_edge_view_v1'
MAX_SOURCE_BYTES=64000
MAX_VIEW_BYTES=2048
MAX_UNITS=32
KEYWORDS={'if','for','while','switch','catch','synchronized','try','new','return','throw','assert','super','this'}
IDENT=re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')

class Unsupported(ValueError):pass

def tokens(source):
    if not isinstance(source,str) or len(source.encode('utf-8'))>MAX_SOURCE_BYTES:
        raise Unsupported('source_limit')
    # Java translates Unicode escapes before tokenization, including comments.
    # Text blocks and non-ASCII identifiers require a fuller Java lexer.
    if re.search(r'\\u+[0-9a-fA-F]{4}',source) or '"""' in source:
        raise Unsupported('lexical_form')
    result=[];i=0;line=1
    while i<len(source):
        c=source[i]
        if c.isspace():
            line+=c=='\n';i+=1;continue
        if source.startswith('//',i):
            end=source.find('\n',i+2);i=len(source) if end<0 else end;continue
        if source.startswith('/*',i):
            end=source.find('*/',i+2)
            if end<0:raise Unsupported('unterminated_comment')
            line+=source[i:end+2].count('\n');i=end+2;continue
        if c in {'"',"'"}:
            quote=c;start_line=line;i+=1
            while i<len(source):
                if source[i]=='\\':i+=2;continue
                if source[i]==quote:break
                if source[i]=='\n':raise Unsupported('unterminated_literal')
                i+=1
            if i>=len(source):raise Unsupported('unterminated_literal')
            result.append(('<literal>',start_line));i+=1;continue
        match=IDENT.match(source,i)
        if match:
            result.append((match[0],line));i=match.end();continue
        if c.isdigit():
            end=i+1
            while end<len(source) and source[end] in '0123456789abcdefABCDEFxX_.lLfFdD':end+=1
            result.append(('<number>',line));i=end;continue
        if c in '{}()[];,.=<>!+-*/%&|^~?:@':
            result.append((c,line));i+=1;continue
        raise Unsupported('lexical_form')
    return result

def pairs(ts):
    stack=[];matched={};depth=[];level=0
    for i,(word,_) in enumerate(ts):
        depth.append(level)
        if word in '{([':
            stack.append((word,i));level+=word=='{'
        elif word in '})]':
            if not stack or stack[-1][0]!={'}':'{',')':'(',']':'['}[word]:
                raise Unsupported('fragment_prefix')
            _,j=stack.pop();matched[j]=i;level-=word=='}'
    # Unmatched openings at the end are a normal bounded excerpt tail.
    return matched,depth

def span(ts,a,b):return [ts[a][1],ts[b][1]]
def is_ident(value):return IDENT.fullmatch(value) is not None

def arity(ts,left,right):
    if right==left+1:return 0
    p=b=s=angle=0;count=1
    for word,_ in ts[left+1:right]:
        if word=='(':p+=1
        elif word==')':p-=1
        elif word=='[':b+=1
        elif word==']':b-=1
        elif word=='{':s+=1
        elif word=='}':s-=1
        elif word=='<':angle+=1
        elif word=='>':angle-=1
        elif word==',' and p==b==s==angle==0:count+=1
        if min(p,b,s,angle)<0:return None
    return count if p==b==s==angle==0 else None

def simple_statement(ts,start,matched):
    i=start
    while i<len(ts):
        word=ts[i][0]
        if word in {'{','}','catch','finally'}:return None
        if word==';':return span(ts,start,i)
        if word in {'(', '['}:
            if i not in matched:return None
            i=matched[i]
        i+=1
    return None

def try_units(ts,matched):
    units=[];incomplete=0
    for i,(word,_) in enumerate(ts):
        if word!='try':continue
        j=i+1;resource=None
        if j<len(ts) and ts[j][0]=='(':
            if j not in matched:incomplete+=1;continue
            end=matched[j];resource=span(ts,j,end);j=end+1
        if j>=len(ts) or ts[j][0]!='{' or j not in matched:
            incomplete+=1;continue
        body_end=matched[j];cursor=body_end+1;catches=[];final=None;valid=True
        while cursor<len(ts) and ts[cursor][0]=='catch':
            p=cursor+1
            if p not in matched or ts[p][0]!='(':
                valid=False;break
            header_end=matched[p];opening=header_end+1
            if opening>=len(ts) or ts[opening][0]!='{' or opening not in matched:
                valid=False;break
            end=matched[opening];catches.append(span(ts,cursor,end));cursor=end+1
        if valid and cursor<len(ts) and ts[cursor][0]=='finally':
            opening=cursor+1
            if opening>=len(ts) or ts[opening][0]!='{' or opening not in matched:valid=False
            else:final=span(ts,cursor,matched[opening]);cursor=matched[opening]+1
        if not valid or (resource is None and not catches and final is None):
            incomplete+=1;continue
        units.append(dict(kind='try_scope',header=span(ts,i,j),resources=resource,
            body=span(ts,j,body_end),body_content=span(ts,j+1,body_end-1) if body_end>j+1 else None,
            catches=catches,finally_span=final,after_chain=simple_statement(ts,cursor,matched)))
    return units,incomplete

def call_units(ts,matched,depth):
    declarations=[i for i,(word,_) in enumerate(ts) if word in {'class','interface','enum','record'}]
    if len(declarations)!=1 or ts[declarations[0]][0]!='class':return [],'requires_single_ordinary_class'
    c=declarations[0]
    if c+1>=len(ts):return [],'incomplete_class'
    class_name=ts[c+1][0]
    opening=next((i for i in range(c+2,len(ts)) if ts[i][0]=='{'),None)
    if opening is None:return [],'incomplete_class'
    method_depth=depth[opening]+1;methods=[]
    for i,(name,_) in enumerate(ts):
        if depth[i]!=method_depth or not is_ident(name) or name in KEYWORDS or name==class_name:continue
        p=i+1
        if p>=len(ts) or ts[p][0]!='(' or p not in matched:continue
        q=matched[p];body=q+1
        if body>=len(ts) or ts[body][0]!='{' or body not in matched:continue
        # Declarations, not member field initializers or qualified invocations.
        if i==0 or not is_ident(ts[i-1][0]) or ts[i-1][0] in {'new','return','throw'}:continue
        n=arity(ts,p,q)
        if n is not None:methods.append(dict(name=name,argc=n,decl=span(ts,i,q),start=body,end=matched[body]))
    by_name={}
    for method in methods:by_name.setdefault(method['name'],[]).append(method)
    # A partial/throws/abstract overload is still an ambiguity: count any
    # declaration-shaped same-level name, even when its body is unavailable.
    name_counts={}
    for i,(name,_) in enumerate(ts):
        if depth[i]==method_depth and is_ident(name) and name not in KEYWORDS and i and is_ident(ts[i-1][0]):
            if i+1<len(ts) and ts[i+1][0]=='(':name_counts[name]=name_counts.get(name,0)+1
    units=[]
    for caller in methods:
        # Do not attribute deferred lambda/anonymous-body work to the enclosing
        # method. This lexical candidate deliberately covers top-level tokens
        # only and omits entire arrow-containing methods, not a call graph.
        if any(ts[j][0]=='-' and ts[j+1][0]=='>' for j in range(caller['start']+1,caller['end']-1)):
            continue
        for i in range(caller['start']+1,caller['end']):
            if depth[i]!=depth[caller['start']]+1:continue
            name=ts[i][0];p=i+1
            if name not in by_name or name_counts.get(name)!=1:continue
            if i and ts[i-1][0] in {'.',':','new'}:continue
            if p>=len(ts) or ts[p][0]!='(' or p not in matched:continue
            callee=by_name[name][0]
            if arity(ts,p,matched[p])!=callee['argc']:continue
            units.append(dict(kind='local_call_candidate',caller=caller['name'],caller_decl=caller['decl'],
                call=span(ts,i,matched[p]),callee=name,callee_decl=callee['decl']))
    return units,'name_and_arity_only_not_dispatch_resolution'

def build_view(evidence):
    """Line ranges describe supplied evidence; they never select cited lines."""
    if not isinstance(evidence,dict) or set(evidence)!={'evidence_id','start_line','excerpt'}:
        raise Unsupported('input_shape')
    if not isinstance(evidence['evidence_id'],str) or not 1<=len(evidence['evidence_id'])<=80:
        raise Unsupported('input_shape')
    start=evidence['start_line']
    if type(start) is not int or not 1<=start<=1000000:raise Unsupported('input_shape')
    ts=tokens(evidence['excerpt']);ts=[(word,line+start-1) for word,line in ts]
    matched,depth=pairs(ts);scopes,incomplete=try_units(ts,matched);edges,resolution=call_units(ts,matched,depth)
    units=scopes+edges
    if len(units)>MAX_UNITS:raise Unsupported('view_limit')
    result=dict(format=FORMAT,evidence_id=evidence['evidence_id'],units=units,incomplete_try_chains=incomplete,
        call_resolution=resolution,call_scope='top_level_tokens_only; arrow_containing_methods_omitted',
        semantics_verified=False,selected_source_lines_unchanged=True)
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-8'))>MAX_VIEW_BYTES:
        raise Unsupported('view_limit')
    return result
