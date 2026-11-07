"""Explicit selected_table_v1 wire. Pure strict decoding; original freeze remains authoritative."""
from copy import deepcopy
import json
import math
import re

from pydantic import ValidationError
from project_evidence.agent_protocol import ModelTurn,ModelError
from project_evidence.followup_claim_parts import freeze_plan as original_freeze
from project_evidence.followup_wire import LINE_PATTERN
from project_evidence.followup_evidence_plan import CLAIM_ROLES
from project_evidence.learning_contract import CitationBoundsError,validate_followup_spans
from project_evidence.followup_line_mentions import validate_explicit_line_mentions
from project_evidence.phase_diagnostics import PhaseContractError,safe_wire_diagnostics
from project_evidence.task_diagnostics import safe_metadata

MAX_RAW_BYTES=262144  # Original transport envelope cap, NOT the phase value cap.
MAX_PHASE_BYTES=128000
IDENTIFIER=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
LABELS={
    'json':('action.json_invalid',),
    'shape':('wire.plan_invalid','wire.plan_fields'),
    'identity':('wire.plan_invalid','wire.plan_kind'),
    'blocks':('wire.blocks_type',),
    'block':('wire.block_type',),
    'references':('wire.references_type',),
    'reference':('wire.reference_keys',),
    'lines':('wire.line_list_syntax',),
    'order':('wire.plan_invalid','wire.plan_evidence_id'),
    'anchors':('wire.plan_invalid','wire.plan_anchor_fields'),
    'pointer':('wire.plan_unselected',),
    'roles':('wire.plan_invalid','wire.plan_role'),
    'parts':('wire.plan_invalid','wire.plan_fields'),
    'schema':('wire.plan_invalid',),
    'citation':('wire.plan_unselected',),
    'limit':('wire.plan_invalid',),
    'implementation':('wire.plan_invalid',),
}

class Rejected(ValueError):
    def __init__(self,reason):
        self.reason=reason if type(reason) is str and reason in LABELS else 'implementation'
        super().__init__('Table candidate rejected')

def stop(reason):raise Rejected(reason)
def need(value,reason):
    if not value:stop(reason)
def obj(value,keys,reason):need(type(value) is dict and set(value)==set(keys),reason)
def seq(value,low,high,reason):need(type(value) is list and low<=len(value)<=high,reason)
def integer(value,low,high,reason):need(type(value) is int and low<=value<=high,reason)
def string(value,low,high,reason):need(type(value) is str and low<=len(value)<=high,reason)

def bounded(value):
    try:raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,UnicodeError,RecursionError):stop('shape')
    need(len(raw)<=MAX_PHASE_BYTES,'limit')

def safe_failure(error):
    """Revalidate mutable exception attributes, never inspect error text/args."""
    reason=getattr(error,'reason',None) if type(error) is Rejected else None
    if type(reason) is not str or reason not in LABELS:reason='implementation'
    wire=safe_wire_diagnostics(LABELS[reason])
    # JSON labels belong to model diagnostics, not phase metadata; the local
    # reason remains bounded. No new production whitelist or key is added.
    metadata=safe_metadata(dict(phase='plan_or_tools',phase_check=dict(step='freeze_plan',reason='wire_rejected',wire_diagnostics=wire)))
    return dict(code='invalid_response',reason=reason,retryable=False,diagnostics=list(LABELS[reason]),metadata=metadata)

def parse(raw):
    """Only the exact action wrapper; duplicate keys fail at every depth.

    Raw text retains the original transport ceiling. Normalized decoded phase
    values retain128000; whitespace is never mistaken for canonical expansion.
    """
    try:
        need(type(raw) in (str,bytes),'json')
        if type(raw) is bytes:
            need(len(raw)<=MAX_RAW_BYTES,'limit');text=raw.decode('utf-8')
        else:
            need(len(raw)<=MAX_RAW_BYTES,'limit');need(len(raw.encode('utf-8'))<=MAX_RAW_BYTES,'limit');text=raw
        def pairs(items):
            out={}
            for key,value in items:
                if key in out:stop('json')
                out[key]=value
            return out
        def constant(_):stop('json')
        value=json.loads(text,object_pairs_hook=pairs,parse_constant=constant)
        # Valid existing shapes are shallower and smaller. This guards parser
        # work only; none of the original phase/source limits are increased.
        nodes=0
        def tree(x,depth=0):
            nonlocal nodes
            nodes+=1;need(nodes<=10000 and depth<=12,'limit')
            if type(x) is dict:
                for child in x.values():tree(child,depth+1)
            elif type(x) is list:
                for child in x:tree(child,depth+1)
            elif type(x) is float:need(math.isfinite(x),'json')
        tree(value)
        obj(value,('action',),'shape');need(type(value['action']) is dict,'shape')
        action=value['action'];bounded(action)
        need('usage' not in action,'shape')  # Same model-usage refusal as the original adapter.
        if action.get('kind')=='citation_plan':return action
        if action.get('kind') not in ('tools','final'):stop('shape')
        ModelTurn.model_validate(action)
        if action['kind']=='final':need(action['final_output']['kind'] in ('gap','clarify'),'shape')
        return action
    except Rejected:raise
    except (ValueError,TypeError,UnicodeError,RecursionError,KeyError):
        raise Rejected('json') from None

def pointer(refs,ri,li):
    integer(ri,1,16,'pointer');integer(li,1,20,'pointer')
    need(ri<=len(refs),'pointer')
    eid,lines=refs[ri-1];need(li<=len(lines),'pointer')
    return eid,lines[li-1]

def decode(value,*,topic,section_id):
    """Decode explicit choices only; canonical validation follows unchanged."""
    bounded(value)
    obj(value,('kind','topic','section_id','title','blocks','gaps'),'shape')
    need(value['kind']=='citation_plan' and type(value['topic']) is str and type(value['section_id']) is str,'shape')
    need(value['topic']==topic and value['section_id']==section_id,'identity')
    string(value['title'],1,80,'schema');seq(value['gaps'],0,12,'schema')
    for gap in value['gaps']:string(gap,1,300,'schema')
    seq(value['blocks'],1,12,'blocks')
    result=deepcopy(value)
    for block in result['blocks']:
        obj(block,('category','support','source_lines','evidence_order','evidence_plan','claim_parts'),'block')
        need(type(block['category']) is str and block['category'] in ('inputs','processing','next_hop','outputs','mechanism','failure','gap'),'schema')
        need(type(block['support']) is str and block['support'] in ('fact','documentation','inference','candidate','gap'),'schema')
        seq(block['source_lines'],0,16,'references');refs=[];ids=set()
        for ref in block['source_lines']:
            obj(ref,('evidence_id','line_numbers'),'reference')
            string(ref['evidence_id'],1,80,'reference');need(IDENTIFIER.fullmatch(ref['evidence_id']) is not None,'reference')
            need(ref['evidence_id'] not in ids,'reference');ids.add(ref['evidence_id'])
            string(ref['line_numbers'],1,159,'lines');need(re.fullmatch(LINE_PATTERN,ref['line_numbers']) is not None,'lines')
            lines=[int(x) for x in ref['line_numbers'].split(',')]
            need(len(lines)==len(set(lines)),'lines');refs.append((ref['evidence_id'],lines))
        seq(block['evidence_order'],0,16,'order');order=block['evidence_order']
        need(all(type(i) is int for i in order),'order')
        need(len(order)==len(refs) and set(order)==set(range(1,len(refs)+1)),'order')
        block['evidence_ids']=[refs[i-1][0] for i in order];block.pop('evidence_order')
        ep=block['evidence_plan'];obj(ep,('claim_kind','anchors'),'anchors')
        kind=ep['claim_kind'];need(type(kind) is str and kind in CLAIM_ROLES,'roles')
        seq(ep['anchors'],0,8,'anchors');decoded=[];seen=set();roles=set()
        for anchor in ep['anchors']:
            obj(anchor,('role','reference_index','line_index'),'anchors')
            role=anchor['role'];need(type(role) is str and role in CLAIM_ROLES[kind],'roles')
            eid,line=pointer(refs,anchor['reference_index'],anchor['line_index'])
            key=(role,eid,line);need(key not in seen,'anchors');seen.add(key);roles.add(role)
            decoded.append(dict(role=role,evidence_id=eid,line_number=line))
        need(roles==set(CLAIM_ROLES[kind]),'roles');ep['anchors']=decoded
        if kind in ('gap','unverified'):
            need(block['support']==('gap' if kind=='gap' else 'candidate') and not refs and not order,'roles')
        else:need(block['support']!='gap','roles')
        seq(block['claim_parts'],1,16,'parts')
        for part in block['claim_parts']:
            need(type(part) is dict,'parts')
            if part.get('kind')=='text':
                obj(part,('kind','text'),'parts');string(part['text'],1,1200,'parts')
            else:
                obj(part,('kind','reference_index','line_index'),'parts');need(part['kind']=='position','parts')
                pointer(refs,part['reference_index'],part['line_index'])
    bounded(result)
    return result

def freeze(value,references,*,topic,section_id):
    """Same original FrozenPlan type/bytes/SHA; fixed errors expose no values."""
    try:
        decoded=decode(value,topic=topic,section_id=section_id)
        def check(out):
            validate_followup_spans(out,references,require_supported=True)
            validate_explicit_line_mentions(out)
        return original_freeze(decoded,references,check)
    except Rejected:raise
    except CitationBoundsError:raise Rejected('citation') from None
    except PhaseContractError as error:
        raise Rejected('limit' if error.reason=='phase_payload_limit' else 'parts') from None
    except ModelError:raise Rejected('roles') from None
    except ValidationError:raise Rejected('schema') from None
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):raise Rejected('schema') from None
