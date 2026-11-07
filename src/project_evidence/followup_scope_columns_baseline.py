"""Experimental model-only key dictionary; no source/payload selection or I/O."""
from copy import deepcopy
import json,re
FORMAT='java_scope_edge_key_dictionary_v1'
SHARED_FORMAT='java_scope_edge_shared_fields_v1'
SHARED_NOTICE='Merge shared_view_fields into each data.views item; all other fields stay exact.'
VIEW_ORDER=('format','evidence_id','units','incomplete_try_chains','call_resolution','call_scope','semantics_verified','selected_source_lines_unchanged')
SHAREABLE=('format','call_resolution','call_scope','semantics_verified','selected_source_lines_unchanged')
def encoded(value):return isinstance(value,dict) and value.get('format') in (FORMAT,SHARED_FORMAT)
NOTICE='keys数组按下标定义data中所有对象键名；仅对象键被替换，数组次序及字符串值逐字不变。解码后含原全部位置、限制与免责声明，不新增引用资格或语义证明。'
def dump(value,compact=True):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,**({'separators':(',',':')} if compact else {}))
def unpack_scope_view(value):
    if isinstance(value,dict) and value.get('format')==SHARED_FORMAT:
        if set(value)!={'format','notice','shared_view_fields','data'} or value['notice']!=SHARED_NOTICE:raise ValueError('shared_shape')
        shared=value['shared_view_fields'];raw=deepcopy(value['data'])
        if type(shared) is not dict or not shared or not set(shared)<=set(SHAREABLE):raise ValueError('shared_fields')
        if type(raw) is not dict or raw.get('format')!='java_scope_edge_view_v1' or type(raw.get('views')) is not list or len(raw['views'])<2:raise ValueError('shared_data')
        for view in raw['views']:
            if type(view) is not dict or set(view)&set(shared) or set(view)|set(shared)!=set(VIEW_ORDER):raise ValueError('shared_view')
        raw['views']=[{k:deepcopy(shared[k] if k in shared else v[k]) for k in VIEW_ORDER} for v in raw['views']]
        return raw
    if not isinstance(value,dict) or set(value)!={'format','notice','keys','data'} or value['format']!=FORMAT or value['notice']!=NOTICE:
        raise ValueError('encoding_shape')
    keys=value['keys']
    if type(keys) is not list or not keys or any(type(k) is not str for k in keys) or len(set(keys))!=len(keys):raise ValueError('key_dictionary')
    def walk(node):
        if type(node) is dict:
            out={}
            for k,v in node.items():
                if type(k) is not str or not re.fullmatch(r'0|[1-9][0-9]*',k) or int(k)>=len(keys):raise ValueError('encoded_key')
                out[keys[int(k)]]=walk(v)
            return out
        if type(node) is list:return [walk(x) for x in node]
        if node is None or type(node) in (str,int,bool):return node
        raise ValueError('encoded_value')
    return walk(value['data'])
def pack_key_dictionary(value):
    if type(value) is not dict or value.get('format')!='java_scope_edge_view_v1':raise ValueError('scope_format')
    keys=[]
    def collect(node):
        if type(node) is dict:
            for k,v in node.items():
                if type(k) is not str:raise ValueError('scope_key')
                if k not in keys:keys.append(k)
                collect(v)
        elif type(node) is list:
            for v in node:collect(v)
        elif node is not None and type(node) not in (str,int,bool):raise ValueError('scope_value')
    collect(value);index={key:str(i) for i,key in enumerate(keys)}
    def walk(node):
        if type(node) is dict:return {index[k]:walk(v) for k,v in node.items()}
        if type(node) is list:return [walk(v) for v in node]
        return node
    packed=dict(format=FORMAT,notice=NOTICE,keys=keys,data=walk(value))
    # A view with little repetition keeps the complete existing presentation.
    # Both production encoders and UTF8 sizes must improve, including legend.
    better=all(len(dump(packed,c))<len(dump(value,c)) and len(dump(packed,c).encode())<len(dump(value,c).encode()) for c in (True,False))
    if not better:return deepcopy(value)
    if dump(unpack_scope_view(packed))!=dump(value):raise ValueError('non_reversible_encoding')
    return packed
def pack_scope_view(value):
    # First validate the complete tree using the strict dictionary encoder.
    dictionary=pack_key_dictionary(value);candidates=[dictionary]
    views=value.get('views')
    if type(views) is list and len(views)>=2 and all(type(v) is dict and tuple(v)==VIEW_ORDER for v in views):
        shared={k:deepcopy(views[0][k]) for k in SHAREABLE if all(dump(v[k])==dump(views[0][k]) for v in views)}
        if shared:
            data=deepcopy(value);data['views']=[{k:deepcopy(v) for k,v in view.items() if k not in shared} for view in views]
            candidates.append(dict(format=SHARED_FORMAT,notice=SHARED_NOTICE,shared_view_fields=shared,data=data))
    eligible=[p for p in candidates if encoded(p) and all(len(dump(p,c))<len(dump(value,c)) and len(dump(p,c).encode())<len(dump(value,c).encode()) for c in (True,False))]
    if not eligible:return deepcopy(value)
    result=min(eligible,key=lambda p:len(dump(p)))
    if dump(unpack_scope_view(result))!=dump(value):raise ValueError('non_reversible_encoding')
    return result
