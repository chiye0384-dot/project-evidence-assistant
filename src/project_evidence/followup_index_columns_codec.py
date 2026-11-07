"""Isolated lossless column representation of the existing lexical index only."""
from copy import deepcopy
import json
FORMAT='java_scope_index_columns_v1'
NOTICE='For each file, scopes rows are [shape_index, values...]. Use scope_columns[shape_index] as field names in order. Every original value, missing field and restriction is preserved.'
ALLOWED=('kind','header_start','header_end','closing_line','complete','header','method_name','try_header_start')
def dump(value,compact=True):return json.dumps(value,ensure_ascii=False,allow_nan=False,**({'separators':(',',':')} if compact else {}))
def tree(value):
    if type(value) is dict:
        for key,item in value.items():
            if type(key) is not str:raise ValueError('nonstring_key')
            tree(item)
    elif type(value) is list:
        for item in value:tree(item)
    elif value is not None and type(value) not in (str,int,bool):raise ValueError('non_json_value')
def encoded(value):return type(value) is dict and value.get('format')==FORMAT
def unpack(value):
    tree(value)
    if not encoded(value) or set(value)!={'format','notice','scope_columns','data'} or value['notice']!=NOTICE:raise ValueError('index_envelope')
    columns=value['scope_columns'];data=deepcopy(value['data'])
    if type(columns) is not list or not columns or len(columns)>16:raise ValueError('column_shapes')
    seen=set()
    for shape in columns:
        if type(shape) is not list or not shape or any(type(k) is not str or k not in ALLOWED for k in shape) or len(set(shape))!=len(shape):raise ValueError('column_shape')
        if tuple(shape) in seen:raise ValueError('duplicate_shape')
        seen.add(tuple(shape))
    if type(data) is not dict or type(data.get('files')) is not list:raise ValueError('index_data')
    used=set()
    for file in data['files']:
        if type(file) is not dict or type(file.get('scopes')) is not list:raise ValueError('index_file')
        restored=[]
        for row in file['scopes']:
            if type(row) is not list or not row or type(row[0]) is not int or not 0<=row[0]<len(columns) or len(row)!=len(columns[row[0]])+1:raise ValueError('index_row')
            used.add(row[0]);restored.append({k:deepcopy(v) for k,v in zip(columns[row[0]],row[1:])})
        file['scopes']=restored
    if used!=set(range(len(columns))):raise ValueError('unused_shape')
    return data
def pack(value):
    tree(value)
    if type(value) is not dict or type(value.get('files')) is not list:raise ValueError('index_shape')
    result=deepcopy(value);columns=[]
    for file in result['files']:
        if type(file) is not dict or type(file.get('scopes')) is not list:return deepcopy(value)
        packed=[]
        for scope in file['scopes']:
            if type(scope) is not dict or not scope or any(k not in ALLOWED for k in scope):return deepcopy(value)
            shape=list(scope)
            if shape not in columns:columns.append(shape)
            if len(columns)>16:return deepcopy(value)
            packed.append([columns.index(shape)]+list(scope.values()))
        file['scopes']=packed
    if not columns:return deepcopy(value)
    candidate=dict(format=FORMAT,notice=NOTICE,scope_columns=columns,data=result)
    if not all(len(dump(candidate,c))<len(dump(value,c)) and len(dump(candidate,c).encode())<len(dump(value,c).encode()) for c in (False,True)):return deepcopy(value)
    if dump(unpack(candidate))!=dump(value):raise ValueError('non_reversible_index')
    return candidate
