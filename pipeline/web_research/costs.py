"""Pre-run estimate from Vercel's public model catalog; no inference credentials needed."""
from datetime import date, datetime, timezone
import json
import os
from urllib.request import urlopen

CATALOG='https://ai-gateway.vercel.sh/v1/models'
PRICING_PAGE='https://vercel.com/ai-gateway/models?modality=text'
TAKO_PRICING='https://vercel.com/docs/ai-gateway/models-and-providers/web-search#using-tako-search'
PROMOTION='https://vercel.com/changelog/tako-search-is-free-on-ai-gateway-through-september-30th'

def calculate(rows, models, catalog, today=None, coordinate_followup=False):
    today=today or date.today()
    rates={m['id']:m['pricing'] for m in catalog['data'] if m['id'] in models}
    # Planning assumptions informed by the ten-row pilot, not a hard spending cap.
    stages=[('search',models[0],35000,1000),('extract',models[1],10000,1500)]
    if coordinate_followup:
        stages += [('registry_search',models[0],35000,1000),
                   ('registry_extract',models[1],10000,1500)]
    details=[]
    for stage,model,inp,out in stages:
        p=rates[model]
        details.append(dict(stage=stage,model=model,input_tokens=rows*inp,output_tokens=rows*out,
            input_per_million_usd=float(p['input'])*1e6,output_per_million_usd=float(p['output'])*1e6,
            estimated_usd=round(rows*(inp*float(p['input'])+out*float(p['output'])),6)))
    model_total=sum(s['estimated_usd'] for s in details)
    search_calls=rows*(6 if coordinate_followup else 3)
    unit=0 if today<=date(2026,9,30) else .007
    search_total=search_calls*unit
    total=model_total+search_total
    return dict(rows=rows,estimated_total_usd=round(total,6),expected_range_usd=[round(total*.5,6),round(total*2,6)],
        model_estimate_usd=round(model_total,6),stages=details,
        tako=dict(estimated_calls=search_calls,rate_per_call_usd=unit,estimated_usd=round(search_total,6),standard_rate_per_call_usd=.007,promotion_ends='2026-09-30'),
        pricing_page=PRICING_PAGE,catalog_url=CATALOG,tako_pricing_url=TAKO_PRICING,promotion_url=PROMOTION,
        estimated_at=datetime.now(timezone.utc).isoformat(),
        assumptions=('Up to 90,000 input / 5,000 output tokens and 6 Tako calls per row when the bounded coordinate-recovery registry follow-up is enabled; otherwise 45,000 input / 2,500 output tokens and 3 Tako calls per row. Uncached base rates. Expected range is not a cap. Retries, context size and internal search loops vary. Excludes GitHub runner costs. No native Google search is used.'))

def estimate(rows, models, out):
    with urlopen(CATALOG,timeout=30) as r: catalog=json.load(r)
    result=calculate(rows,models,catalog,coordinate_followup=os.environ.get('RESEARCH_COORDINATE_RECOVERY') == '1')
    (out/'cost-estimate.json').write_text(json.dumps(result,indent=2))
    line=f"Estimated cost before research: ${result['estimated_total_usd']:.4f} for {rows} rows (expected range ${result['expected_range_usd'][0]:.4f}–${result['expected_range_usd'][1]:.4f}; not a cap)."
    print(line,flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Cost estimate before research\n\n'+line+'\n\nModel and Tako costs are broken out in cost-estimate.json.\n\n')
    return result
