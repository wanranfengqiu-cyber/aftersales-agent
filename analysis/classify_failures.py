import json, sys
from collections import Counter, defaultdict
d=json.load(open(sys.argv[1] if len(sys.argv)>1 else 'results.json', encoding='utf-8'))
sims=d['simulations']; tasks={t['id']:t for t in d['tasks']}
WRITE={'cancel_pending_order','exchange_delivered_order_items','modify_pending_order_address','modify_pending_order_items','modify_pending_order_payment','modify_user_address','return_delivered_order_items'}
def norm(a):
    return json.dumps({k:(sorted(v) if isinstance(v,list) else v) for k,v in (a or {}).items()},sort_keys=True,ensure_ascii=False)
def calls(s):
    out=[]
    for m in s['messages']:
        for tc in (m.get('tool_calls') or []):
            out.append((tc['name'], tc.get('arguments') or {}))
    return out
def classify(s):
    t=tasks[s['task_id']]; ri=s['reward_info']
    exp=[(a['name'],a['arguments']) for a in t['evaluation_criteria']['actions'] if a['name'] in WRITE]
    cs=calls(s); act=[(n,a) for n,a in cs if n in WRITE]
    E=Counter((n,norm(a)) for n,a in exp); A=Counter((n,norm(a)) for n,a in act)
    missing=E-A; extra=A-E
    db=ri.get('db_check') or {}
    if db.get('db_match'):
        return '数据库对,但告知/NL断言没过', ''
    if not cs:
        return '一个工具都没调(多为身份核验没完成)', ''
    if any(n=='transfer_to_human_agents' for n,_ in cs) and not act and exp:
        return '转人工,没做该做的写操作', ''
    mn={n for n,_ in missing}; en={n for n,_ in extra}
    if missing and extra and mn&en:
        return '写操作参数不对', f"应:{list(missing)[0][1][:120]} 实:{list(extra)[0][1][:120]}"
    if extra and not missing:
        return '多做了写操作', str([n for n,_ in extra])
    if missing and not extra:
        return '漏做了写操作', str([n for n,_ in missing])
    if missing and extra:
        return '做错了写操作(工具不同)', f"应:{[n for n,_ in missing]} 实:{[n for n,_ in extra]}"
    return '写操作都对但数据库不对(其它)', ''
rows=defaultdict(list)
for s in sims:
    if s['reward_info']['reward']!=1.0:
        rows[s['task_id']].append((s['trial'],)+classify(s))
succ=Counter(s['task_id'] for s in sims if s['reward_info']['reward']==1.0)
cat=Counter(); taskcat=defaultdict(Counter)
for tid,lst in rows.items():
    for tr,c,det in lst:
        cat[c]+=1; taskcat[tid][c]+=1
print('失败运行总数', sum(cat.values()))
for c,n in cat.most_common(): print(f'{n:3d}  {c}')
print()
for tid in sorted(rows,key=int):
    print(tid, f'{succ[tid]}/4', dict(taskcat[tid]), '|', [det for _,c,det in rows[tid] if det][:1])

print('\n===== 漏做 / 转人工 / 没调工具:看对话结尾 =====')
for s in sims:
    if s['reward_info']['reward']==1.0: continue
    c,_=classify(s)
    if c not in ('漏做了写操作','转人工,没做该做的写操作','一个工具都没调(多为身份核验没完成)'): continue
    ms=[m for m in s['messages'] if m['role'] in ('assistant','user') and m.get('content')]
    la=[m for m in ms if m['role']=='assistant'][-1]['content'][:230].replace('\n',' ')
    lu=[m for m in ms if m['role']=='user'][-1]['content'][:160].replace('\n',' ')
    tr=any(tc['name']=='transfer_to_human_agents' for m in s['messages'] for tc in (m.get('tool_calls') or []))
    print(f"[{s['task_id']} t{s['trial']}] {c}{' +转人工' if tr else ''} | 轮数{len(ms)}\n  A末: {la}\n  U末: {lu}")

import re
YES=re.compile(r'\b(yes|proceed|go ahead|please (do|cancel|update|return|exchange|confirm))\b',re.I)
def fine(s):
    c,det=classify(s)
    ms=[m for m in s['messages'] if m['role'] in ('assistant','user') and m.get('content')]
    lu=[m for m in ms if m['role']=='user'][-1]['content']
    tr=any(tc['name']=='transfer_to_human_agents' for m in s['messages'] for tc in (m.get('tool_calls') or []))
    if c=='漏做了写操作' and '###STOP###' in lu and YES.search(lu):
        return '模拟器:顾客确认的同一句里就结束对话,Agent 来不及执行'
    if c=='一个工具都没调(多为身份核验没完成)':
        return '模拟器:顾客给不出姓名+邮编/邮箱,无法核验身份'
    if c in ('漏做了写操作','转人工,没做该做的写操作') and ('###TRANSFER###' in lu or tr):
        return 'Agent/模拟器:转人工收场(需逐条判断)'
    if c=='漏做了写操作': return 'Agent:漏做写操作'
    if c=='写操作参数不对':
        if '"reason"' in det: return 'Agent参数错:取消原因选错'
        try:
            a,b=det.split(' 实:')
        except: return 'Agent参数错:其它'
        if a[3:60]==b[:57] and 'payment' in det and a.split('payment_method_id')[-1][:20]!=b.split('payment_method_id')[-1][:20]: return 'Agent参数错:支付方式选错'
        return 'Agent参数错:商品款式/订单选错'
    return 'Agent:'+c
F=Counter(); FT=defaultdict(list)
for s in sims:
    if s['reward_info']['reward']==1.0: continue
    k=fine(s); F[k]+=1; FT[k].append(f"{s['task_id']}t{s['trial']}")
print('\n===== 细分 =====')
for k,n in F.most_common(): print(f'{n:3d}  {k}  {FT[k]}')
