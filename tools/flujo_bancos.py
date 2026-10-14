import xlrd, openpyxl, glob, json, re, os, sys, datetime as dt, unicodedata, collections
def norm(s): return unicodedata.normalize('NFD',str(s)).encode('ascii','ignore').decode().upper().strip()
def rows_of(p):
    if p.lower().endswith('.xls'):
        try:
            b=xlrd.open_workbook(p); s=b.sheet_by_index(0)
            out=[]
            for r in range(s.nrows):
                row=[]
                for c in range(s.ncols):
                    v=s.cell(r,c)
                    row.append(xlrd.xldate_as_datetime(v.value,b.datemode) if v.ctype==3 else v.value)
                out.append(row)
            return out
        except Exception as e:
            # html disguised
            return None
    ws=openpyxl.load_workbook(p,read_only=True,data_only=True).worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]
def monto(v):
    if isinstance(v,(int,float)): return float(v)
    t=re.sub(r'[^0-9.,-]','',str(v or ''))
    if not t: return None
    neg='-' in t; t=t.replace('-','')
    if ',' in t and '.' in t: t=t.replace('.','').replace(',','.') if t.rfind(',')>t.rfind('.') else t.replace(',','')
    elif ',' in t: t=t.replace(',','.')
    try: n=float(t)
    except: return None
    return -n if neg else n
def fecha(v):
    if isinstance(v,(dt.datetime,dt.date)): return v.strftime('%Y-%m-%d')
    m=re.match(r'\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})',str(v or ''))
    if m:
        y=m.group(3); y='20'+y if len(y)==2 else y
        return f'{y}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    m=re.match(r'\s*(\d{4})-(\d{2})-(\d{2})',str(v or ''))
    return m.group(0).strip() if m else None
movs={}; resumen=collections.Counter(); arch=[]
files=[]
for pat in ['Extracto_CC*','Ultimos*movimientos*','Ultimos_Movimientos*','*ovimientos*']:
    files+=glob.glob('C:/Users/tienda/Downloads/'+pat)
files=sorted(set(f for f in files if re.search(r'\.(xlsx?|csv)$',f,re.I)))
for p in files:
    rows=rows_of(p)
    if not rows: print('no leido',p); continue
    head=' '.join(norm(' '.join(map(str,r))) for r in rows[:8])
    hr=None
    for i,r in enumerate(rows[:40]):
        n=[norm(x) for x in r]
        if any(x.startswith('FECHA') for x in n) and any(re.match(r'^(CREDITO|MONTO|IMPORTE)',x) for x in n): hr=i;break
    if hr is None: print('sin encabezado',os.path.basename(p)); continue
    H=[norm(x) for x in rows[hr]]
    cf=next(i for i,x in enumerate(H) if x.startswith('FECHA'))
    cc=next((i for i,x in enumerate(H) if x.startswith('CREDITO')),None)
    cm=next((i for i,x in enumerate(H) if re.match(r'^(MONTO|IMPORTE)',x)),None)
    cd=next((i for i,x in enumerate(H) if re.match(r'^(DESCRIPCION|CONCEPTO|DETALLE)',x)),None)
    cr=next((i for i,x in enumerate(H) if re.search(r'(COMPROBANTE|REFERENCIA)',x)),None)
    ln=[i for i,x in enumerate(H) if x.startswith('LEYENDAS ADICIONALES')]
    hall=' '.join(H)
    if 'LEYENDAS ADICIONALES' in hall: banco='Galicia'
    elif 'CAUSAL' in hall: banco='Provincia'
    elif 'NACION' in head or 'BNA' in head: banco='Nación'
    else: banco='Banco'
    m=re.search(r'EMPRESA:\s*\d*\s*-?\s*([A-Z .]{4,40})',' '.join(norm(' '.join(map(str,r))) for r in rows[-8:]+rows[:8]))
    cta=re.search(r'_(CC\d+)',os.path.basename(p))
    emp=(m.group(1).split(' OPERADOR')[0].strip() if m else '')
    cuenta=banco+(' '+cta.group(1) if cta else '')+(' - '+emp if emp else '')
    n=0
    for r in rows[hr+1:]:
        if len(r)<=cf: continue
        d=fecha(r[cf])
        if not d: continue
        imp=monto(r[cc]) if cc is not None else None
        if imp is None and cm is not None: imp=monto(r[cm])
        if not imp or imp<=0: continue
        det=str(r[cd]).strip() if cd is not None and r[cd] is not None else ''
        quien=' '.join(str(r[i]).strip() for i in ln[:2] if i<len(r) and r[i]) if ln else ''
        ref=str(r[cr]).strip() if cr is not None and r[cr] is not None else ''
        k=(cuenta,d,round(imp,2),ref,det,quien)
        movs[k]=1; n+=1
    arch.append(os.path.basename(p)); resumen[cuenta]+=n
agg={}
for (cuenta,d,imp,ref,det,quien) in movs:
    con=re.sub(r'\s+',' ',re.sub(r'[\d:/#-]+',' ',det.split(' · ')[0])).strip()[:40] or 'Credito'
    if re.match(r'(?i)^cr transferencia recibida',con): con='CR Transferencia Recibida'
    if re.match(r'(?i)^g\.? ?de echeq',con): con='Gestion de Echeq'
    if re.match(r'(?i)^dep\.?efvo',con): con='Deposito En Efectivo'
    k=(d,cuenta,con); a=agg.setdefault(k,[0,0]); a[0]+=imp; a[1]+=1
bancos={}
for (d,cuenta,con),(imp,n) in agg.items(): bancos.setdefault(d,[]).append([cuenta,con,round(imp,2),n])
print(len(movs),'movs',len(agg),'grupos',dict(resumen))
json.dump({'bancos':bancos,'archivos':arch},open(sys.argv[1],'w',encoding='utf-8'),ensure_ascii=False,separators=(',',':'))
