"""Isolated coordinate-only supplement. No I/O, source lookup or response repair.

task_evidence_index is one-based in the supplied accepted-reference insertion
order. A future caller must bind this order to the saved task.evidence order.
Multiple matches remain multiple matches; an index never proves entailment.
"""

FIELDS={'block_reference_index','task_evidence_index','selected_line_count','missing_observed_line_sample'}

def safe_slots(value):
    if not isinstance(value,list) or len(value)>16:return []
    result=[];seen=set()
    for row in value:
        if not isinstance(row,dict) or set(row)!=FIELDS:return []
        b,t,n=(row[k] for k in ('block_reference_index','task_evidence_index','selected_line_count'))
        numbers=row['missing_observed_line_sample']
        if (type(b) is not int or not 1<=b<=16 or b in seen or type(t) is not int or not 1<=t<=16
                or type(n) is not int or not 0<=n<=20 or not isinstance(numbers,list) or len(numbers)>20
                or any(type(x) is not int or not 1<=x<=1000000 for x in numbers)
                or len(set(numbers))!=len(numbers)):
            return []
        seen.add(b);result.append(dict(block_reference_index=b,task_evidence_index=t,
            selected_line_count=n,missing_observed_line_sample=list(numbers)))
    return result

def evidence_slots(error,content,references):
    from .task_diagnostics import citation_details
    base=citation_details(error,content,references)
    if not base or not isinstance(references,dict) or not 1<=len(references)<=16:return []
    missing=base.get('missing_observed_line_sample',[])
    block=content.blocks[base['block_index']-1]
    order={eid:i for i,eid in enumerate(references,1)}
    rows=[]
    for i,selection in enumerate(block.source_lines,1):
        reference=references.get(selection.evidence_id)
        if not isinstance(reference,dict) or reference.get('truncated') is not False:continue
        start,end=reference.get('start_line'),reference.get('end_line')
        if type(start) is not int or type(end) is not int or not 1<=start<=end<=1000000:continue
        rows.append(dict(block_reference_index=i,task_evidence_index=order[selection.evidence_id],
            selected_line_count=len(selection.lines),missing_observed_line_sample=[n for n in missing if start<=n<=end]))
    return safe_slots(rows)
