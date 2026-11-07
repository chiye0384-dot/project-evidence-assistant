"""Explicit unit rows with named columns; every value remains reconstructible."""
from copy import deepcopy
import json
from . import followup_scope_columns_baseline as previous
FORMAT='java_scope_edge_unit_columns_v1'
RAW_FORMAT='java_scope_edge_view_v1'
NOTICE='Each units row starts with kind, then unit_columns[kind] values in order. Merge view_defaults into each view. All other fields stay exact.'
UNIT_COLUMNS={
    'try_scope':('header','resources','body','body_content','catches','finally_span','after_chain'),
    'local_call_candidate':('caller','caller_decl','call','callee','callee_decl'),
}
dump=previous.dump
def encoded(value):return type(value) is dict and (value.get('format')==FORMAT or previous.encoded(value))
def valid_tree(node):
    if type(node) is dict:
        for k,v in node.items():
            if type(k) is not str:raise ValueError('object_key')
            valid_tree(v)
    elif type(node) is list:
        for v in node:valid_tree(v)
    elif node is not None and type(node) not in (str,int,bool):raise ValueError('non_json_value')
def unpack_scope_view(value):
    if type(value) is not dict or value.get('format')!=FORMAT:return previous.unpack_scope_view(value)
    valid_tree(value)
    if set(value)!={'format','notice','unit_columns','view_defaults','data'} or value['notice']!=NOTICE:raise ValueError('columns_envelope')
    columns=value['unit_columns'];defaults=value['view_defaults'];raw=deepcopy(value['data'])
    if type(columns) is not dict or not columns or any(k not in UNIT_COLUMNS or type(v) is not list or tuple(v)!=UNIT_COLUMNS[k] for k,v in columns.items()):raise ValueError('unit_columns')
    if type(defaults) is not dict or not set(defaults)<=set(previous.SHAREABLE):raise ValueError('view_defaults')
    if type(raw) is not dict or raw.get('format')!=RAW_FORMAT or type(raw.get('views')) is not list:raise ValueError('columns_data')
    views=[];used=set()
    for view in raw['views']:
        if type(view) is not dict or set(view)&set(defaults) or set(view)|set(defaults)!=set(previous.VIEW_ORDER):raise ValueError('columns_view')
        restored={k:deepcopy(defaults[k] if k in defaults else view[k]) for k in previous.VIEW_ORDER}
        if type(restored['units']) is not list:raise ValueError('unit_rows')
        units=[]
        for row in restored['units']:
            if type(row) is not list or not row or type(row[0]) is not str or row[0] not in columns or len(row)!=1+len(columns[row[0]]):raise ValueError('unit_row')
            kind=row[0];used.add(kind);units.append(dict(kind=kind,**{k:deepcopy(v) for k,v in zip(columns[kind],row[1:])}))
        restored['units']=units;views.append(restored)
    if used!=set(columns):raise ValueError('unused_unit_columns')
    raw['views']=views
    return raw
def pack_scope_view(value):
    valid_tree(value)
    if type(value) is not dict or value.get('format')!=RAW_FORMAT:raise ValueError('scope_format')
    # Retain the previously verified complete representation if the new legend
    # costs more. No unit, source, restriction, or evidence may be omitted.
    old=previous.pack_scope_view(value);views=value.get('views')
    if type(views) is not list or not views or not all(type(v) is dict and tuple(v)==previous.VIEW_ORDER and type(v['units']) is list for v in views):return old
    kinds=[]
    for view in views:
        for unit in view['units']:
            if type(unit) is not dict or unit.get('kind') not in UNIT_COLUMNS:return old
            kind=unit['kind']
            if tuple(unit)!=('kind',)+UNIT_COLUMNS[kind]:return old
            if kind not in kinds:kinds.append(kind)
    if not kinds:return old
    defaults={k:deepcopy(views[0][k]) for k in previous.SHAREABLE if len(views)>=2 and all(dump(v[k])==dump(views[0][k]) for v in views)}
    raw=deepcopy(value)
    for view in raw['views']:
        view['units']=[[unit['kind']]+[deepcopy(unit[k]) for k in UNIT_COLUMNS[unit['kind']]] for unit in view['units']]
    if defaults:raw['views']=[{k:v for k,v in view.items() if k not in defaults} for view in raw['views']]
    packed=dict(format=FORMAT,notice=NOTICE,unit_columns={k:list(UNIT_COLUMNS[k]) for k in kinds},view_defaults=defaults,data=raw)
    if not all(len(dump(packed,c))<len(dump(old,c)) and len(dump(packed,c).encode())<len(dump(old,c).encode()) for c in (True,False)):return old
    if dump(unpack_scope_view(packed))!=dump(value):raise ValueError('non_reversible_columns')
    return packed
