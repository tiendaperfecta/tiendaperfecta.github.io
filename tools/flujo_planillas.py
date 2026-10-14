import xlrd, openpyxl, glob, json, re, os, sys, datetime as dt, unicodedata
OUT=sys.argv[1]
def norm(s):
    s=unicodedata.normalize('NFD',str(s)).encode('ascii','ignore').decode().upper().strip()
    return re.sub(r'\s+',' ',s)
CANON=[ # key, label, matchers, cheque
 ('sueldos','SUELDOS "A" +B',[r'^SUELDOS'],0),
 ('premios','PREMIOS SEMANALES',[r'^PREMIOS'],0),
 ('horas','HORAS EXTRAS DEPÓSITO',[r'^HORAS EXTRAS'],0),
 ('aportes','IMP + APORTES (931)',[r'APORTES'],0),
 ('viaticos','VIÁTICOS',[r'^VIATICOS'],0),
 ('alquiler','ALQUILER DEPO A+B',[r'^ALQUILER'],0),
 ('fletes','FLETES (SEMANAL)',[r'^FLETES'],0),
 ('garrafas','GARRAFAS',[r'^GARRAFAS'],0),
 ('contenedores','CONTENEDORES',[r'^CONTENEDORES'],0),
 ('sistemas','DIGIP+AXUM+GESCOM+INTERNET',[r'^DIGIP'],0),
 ('comafi','PRÉSTAMO COMAFI',[r'^PRESTAMO COMAFI'],0),
 ('echeqcomafi','ECHEQ COMAFI',[r'^ECHEQ COMAFI'],0),
 ('estudio','ESTUDIO CONTABLE Y LEGAL',[r'^ESTUDIO'],0),
 ('chq_tp','E-CHEQS TP (Pepsico)',[r'^E-CHEQS (PEPSICO|TP|TIENDA)$'],1),
 ('chq_rl','E-CHEQS RL (Rambla)',[r'^E-CHEQS (RAMBLA|RL)$'],1),
 ('despidos','DESPIDOS',[r'^DESPIDOS'],0),
 ('prestamos','PRÉSTAMOS',[r'^PRESTAMOS$'],0),
 ('moratorias','MORATORIAS',[r'^MORATORIAS'],0),
 ('impuestos','IMPUESTOS (IIBB + TASA MUNI)',[r'^IMPUESTOS'],0),
 ('softys','SOFTYS',[r'^SOFTYS'],2),
 ('pepsico','PEPSICO',[r'^PEPSICO$'],2),
 ('georgalos','GEORGALOS',[r'^GEORGALOS'],2),
 ('chacabuco','CHACABUCO',[r'^CHACABUCO'],2),
 ('bimbo','BIMBO',[r'^BIMBO'],2),
 ('ecovita','ECOVITA',[r'^ECOVITA'],2),
 ('entrenuts','ENTRENUTS',[r'^ENTRENUTS'],2),
 ('pilaga','PILAGA/L3N',[r'^PILAGA'],2),
 ('resto','RESTO PROVEEDORES',[r'^RESTO'],2),
]
STOP=re.compile(r'^(TOTAL|RECAUDACION|SALDO|CHEQUES A PAGAR|ECHEQS|%)')
MESES=['ENERO','FEBRERO','MARZO','ABRIL','MAYO','JUNIO','JULIO','AGOSTO','SEPTIEMBRE','OCTUBRE','NOVIEMBRE','DICIEMBRE']
def key_for(h):
    n=norm(h)
    for k,l,ms,ch in CANON:
        if any(re.search(m,n) for m in ms): return k
    return None
data={'columnas':[{'key':k,'nombre':l,'tipo':('cheque' if ch==1 else 'proveedor' if ch==2 else 'gasto')} for k,l,ms,ch in CANON],
      'pagos':{}, 'estimado':{}, 'vencido':{}, 'saldoInicial':{}, 'fuentes':[]}
for f in sorted(glob.glob('W:/FLUJO DE CAJA/*2026*.xls')):
    mes=next(i+1 for i,m in enumerate(MESES) if m in norm(os.path.basename(f)))
    ym=f'2026-{mes:02d}'
    b=xlrd.open_workbook(f); s=b.sheet_by_index(0)
    hr=next(r for r in range(10) if norm(s.cell_value(r,0)).startswith('VENC'))
    cols={}; rec=None; tot=None; sal=None
    for c in range(1,s.ncols):
        h=str(s.cell_value(hr,c)).strip()
        if not h: continue
        n=norm(h)
        if n.startswith('RECAUDACION'): rec=c; continue
        if n.startswith('TOTAL PAGOS'): tot=c; continue
        if n.startswith('SALDO'): sal=c; continue
        if STOP.match(n): continue
        k=key_for(h)
        if not k: print('SIN MAPEO',ym,h); continue
        cols[c]=k
    first=True
    for r in range(hr+1,s.nrows):
        a=s.cell(r,0)
        if a.ctype==3:
            d=xlrd.xldate_as_datetime(a.value,b.datemode).strftime('%Y-%m-%d')
            for c,k in cols.items():
                v=s.cell_value(r,c)
                if isinstance(v,float) and abs(v)>0.004:
                    data['pagos'].setdefault(d,{})
                    data['pagos'][d][k]=round(data['pagos'][d].get(k,0)+v,2)
            if rec is not None:
                v=s.cell_value(r,rec)
                if isinstance(v,float) and v: data['estimado'][d]=round(v,2)
            if first and sal is not None:
                # saldo inicial = saldo - rec + pagos + cheques del primer dia
                sv=s.cell_value(r,sal); 
                if isinstance(sv,float):
                    rv=s.cell_value(r,rec) if rec is not None else 0; rv=rv if isinstance(rv,float) else 0
                    pg=sum(s.cell_value(r,c) for c in cols if isinstance(s.cell_value(r,c),float))
                    vv=s.cell_value(r-1,tot) if tot is not None and isinstance(s.cell_value(r-1,tot),float) else 0
                    data['saldoInicial'][ym]=round(sv-rv+pg+vv,2)
                first=False
        elif norm(a.value).startswith('VENCIDO'):
            row={}
            for c,k in cols.items():
                v=s.cell_value(r,c)
                if isinstance(v,float) and v: row[k]=round(v,2)
            if not row and tot is not None and isinstance(s.cell_value(r,tot),float) and s.cell_value(r,tot):
                row['resto']=round(s.cell_value(r,tot),2)
            if row: data['vencido'][ym]={'texto':str(a.value).strip(),'montos':row}
    data['fuentes'].append(os.path.basename(f))
# --- FLETES: se pagan los JUEVES (pedido de Franco, 14/10/2026). Desde octubre 2026 las planillas
# traen un PROMEDIO ($9 M) copiado en los dias 7, 14, 21 y 28 de cada mes, que caen cualquier dia
# de la semana. Cada monto pasa al jueves de su semana (lunes a domingo; si el jueves es feriado,
# al miercoles) y las semanas sin monto se completan con el promedio anotado en ese mes. Antes de
# octubre se dejan como estan: son los pagos reales.
FLETES_DESDE = dt.date(2026, 10, 1)
FERIADOS_JUEVES = {'2026-01-01', '2026-04-02', '2027-03-25', '2027-04-01'}
def dia_de_pago_fletes(d):
    jue = d + dt.timedelta(days=3 - d.weekday())
    while jue.isoformat() in FERIADOS_JUEVES:
        jue -= dt.timedelta(days=1)
    return jue
def corregir_fletes(pagos):
    montos = {}
    for f in sorted(pagos):
        d = dt.date.fromisoformat(f)
        if d >= FLETES_DESDE and pagos[f].get('fletes'):
            montos[f] = pagos[f].pop('fletes')
            if not pagos[f]:
                del pagos[f]
    if not montos:
        return {}
    promedio = {}
    for f, m in montos.items():
        promedio.setdefault(f[:7], []).append(m)
    promedio = {k: round(sum(v) / len(v), 2) for k, v in promedio.items()}
    por_jueves = {}
    for f, m in montos.items():
        j = dia_de_pago_fletes(dt.date.fromisoformat(f))
        por_jueves[j] = por_jueves.get(j, 0) + m
    notas = {}
    ultimo = max(dt.date.fromisoformat(f) for f in montos)
    fin = dt.date(ultimo.year + (ultimo.month == 12), ultimo.month % 12 + 1, 1) - dt.timedelta(days=1)
    lunes = FLETES_DESDE - dt.timedelta(days=FLETES_DESDE.weekday())
    while lunes <= fin:
        j = dia_de_pago_fletes(lunes)
        if j >= FLETES_DESDE and j <= fin:
            mes = j.isoformat()[:7]
            previos = [k for k in promedio if k <= mes]
            prom = promedio.get(mes) or (promedio[max(previos)] if previos else None)
            if j not in por_jueves and prom:
                por_jueves[j] = prom
            if j in por_jueves:
                pagos.setdefault(j.isoformat(), {})['fletes'] = round(por_jueves[j], 2)
                notas[j.isoformat() + '|fletes'] = 'Fletes: se pagan los jueves. Monto PROMEDIO estimado (de la planilla), no el real.'
        lunes += dt.timedelta(days=7)
    print('fletes: %d montos de la planilla pasados a %d jueves' % (len(montos), len(notas)))
    return notas
data['notas'] = corregir_fletes(data['pagos'])
# --- caja tesoreria
def caja_file(mes):
    c=[p for p in glob.glob('W:/Tesoreria/Finanzas_Diarias_*.xlsx') if MESES[mes-1][:4] in norm(os.path.basename(p))]
    c.sort(key=os.path.getmtime); return c[-1] if c else None
caja={}
for mes in range(1,13):
    p=caja_file(mes)
    if not p: continue
    ws=openpyxl.load_workbook(p,read_only=True,data_only=True)['Movimientos']
    n=0
    for i,row in enumerate(ws.iter_rows(values_only=True)):
        if i==0 or not row or not row[0]: continue
        fecha,tipo,cat,sub,desc=row[0],row[1],row[2],row[3],row[4]
        imp=row[10]
        if not isinstance(fecha,(dt.date,dt.datetime)) or str(tipo).strip().lower()!='ingreso': continue
        try: imp=float(imp)
        except: continue
        if not imp: continue
        d=fecha.strftime('%Y-%m-%d')
        if not d.startswith(f'2026-{mes:02d}'): continue
        caja.setdefault(d,[]).append([str(desc or '').strip(), str(cat or '').strip(), round(imp,2)])
        n+=1
    data['fuentes'].append(f'{os.path.basename(p)} ({n} ingresos)')
data['caja']=caja
json.dump(data,open(OUT,'w',encoding='utf-8'),ensure_ascii=False,separators=(',',':'))
print('ok', len(data['pagos']), len(caja), data['saldoInicial'], {k:v['texto'] for k,v in data['vencido'].items()})
