import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
REPOS = _HERE if _os.path.isdir(_os.path.join(_HERE, "PatentSearch-API")) else _os.path.dirname(_HERE)

import json
c=json.load(open(_os.path.join(_HERE,'endpoint_contract.json'), encoding='utf-8'))
oa=json.load(open(_os.path.join(REPOS,'PatentSearch-API','API','static','openapi.json'), encoding='utf-8'))
schemas=oa['components']['schemas']
def oa_fields(props,prefix=''):
    out={}
    for k,v in props.items():
        if v.get('type')=='array' and 'items' in v and 'properties' in v['items']:
            out[prefix+k]='nested'; out.update(oa_fields(v['items']['properties'],prefix+k+'.'))
        else: out[prefix+k]=v.get('type')+('/'+v['format'] if 'format' in v else '')
    return out
url2view={e['url']:k for k,e in c['endpoints'].values() and c['endpoints'].items()}
summary={"openapi_paths":len(oa['paths']),"code_routes":len(c['endpoints']),
         "in_code_not_openapi":sorted(e['url'] for e in c['endpoints'].values() if e['url'].replace('<str:pk>','{'+'x}').split('{')[0] not in {p.split('{')[0] for p in oa['paths']} )}
for p,v in oa['paths'].items():
    if '{' in p: continue
    ref=v['get']['responses']['200']['content']['application/json']['schema']['$ref'].split('/')[-1]
    sch=schemas[ref]['properties']; key=[k for k in sch if k not in ('error','count','total_hits')][0]
    of=oa_fields(sch[key]['items']['properties'])
    e=c['endpoints'][url2view[p]]
    cf={}
    for k,s in e['fields'].items():
        if s.get('type')=='nested_list':
            cf[k]='nested'
            for ck,cs in s['fields'].items(): cf[k+'.'+ck]=cs.get('type')
        else: cf[k]=s.get('type')
    params={pp['name']:pp['schema'].get('default') for pp in v['get']['parameters']}
    e['openapi']={"response_schema":ref,"response_key":key,"field_count":len(of),
                  "openapi_only_fields":sorted(set(of)-set(cf)),"code_only_fields":sorted(set(cf)-set(of)),
                  "default_f":json.loads(params['f']) if params.get('f') else None,
                  "default_s":json.loads(params['s']) if params.get('s') else None,
                  "default_o":json.loads(params['o']) if params.get('o') else None,
                  "field_types":of}
c['openapi_crosscheck']=summary
json.dump(c,open(_os.path.join(_HERE,'endpoint_contract.json'),'w', encoding='utf-8'),indent=1)
print(summary)
