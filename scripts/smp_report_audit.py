"""Compare the app's default results for Micromeritics SMP files with their XLS reports.

Usage: python -m scripts.smp_report_audit "<folder>" ["<folder>" ...] [--output audit.json]
Pairs NAME.SMP with NAME.xls/.XLS (read-only) and checks sample mass,
equilibration interval, BET area and C, single-point BET, Langmuir area and
t-Plot external area / micropore area / micropore volume as the main window
computes them by default. "ok" = within tolerance, "-" = not in the report.
Reports exported before the file's saved selection was changed show up as
differences; open the SMP in the vendor software to confirm.
"""
import os, sys, re, glob, json, math
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from unittest.mock import patch, Mock
import xlrd
from PyQt5 import QtWidgets, QtCore
from tristar_bet import load_file
from tristar_bet.analysis import single_point_bet_analysis
from tristar_bet.ui.main_window import MainWindow, single_point_target

def num(s):
    m=re.search(r'-?[0-9][0-9,]*\.?[0-9]*(?:e[+-]?\d+)?', str(s), re.I)
    return float(m.group().replace(',','')) if m else None

def report(path):
    b=xlrd.open_workbook(str(path)); sh=b.sheets()[0]; out={}
    for r in range(sh.nrows):
        for c in range(sh.ncols-1):
            k=str(sh.cell_value(r,c)).strip()
            if not k.endswith(':'): continue
            v=str(sh.cell_value(r,c+1)).strip()
            kl=k.lower()
            m=re.match(r'single point surface area at p/po = ([0-9.]+):', kl)
            if m: out.setdefault('sp_x', float(m.group(1))); out.setdefault('sp_area', num(v)); continue
            m=re.match(r'less than ([0-9.]+) nm diameter at p/po = ([0-9.]+):', kl)
            if m: out.setdefault('tpv_x', float(m.group(2))); out.setdefault('tpv', num(v)); continue
            out.setdefault(kl, v)
    return out

app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import tempfile
_settings_dir=tempfile.TemporaryDirectory()
settings=QtCore.QSettings(str(Path(_settings_dir.name)/'s.ini'), QtCore.QSettings.IniFormat)
with patch.object(MainWindow,'_auto_check_for_updates'), patch.object(MainWindow,'_initialize_telemetry'), patch('tristar_bet.ui.main_window.QtCore.QSettings', return_value=settings):
    w=MainWindow()
w.refresh_isotherm_plot=Mock(); w.refresh_analysis_plots=Mock()

def rel(a,b,tol):
    if a is None or b is None: return 'MISSING' if b is not None else '-'
    return 'ok' if abs(a-b)<=tol*max(1.0,abs(b)) else f'BAD({a:.6g} vs {b:.6g})'

rows=[]
import argparse
_ap=argparse.ArgumentParser(description=__doc__.splitlines()[0])
_ap.add_argument('folders', nargs='+', type=Path)
_ap.add_argument('--output', type=Path)
_args=_ap.parse_args()
_files=sorted({q for folder in _args.folders for q in folder.iterdir() if q.suffix.lower()=='.smp'})
for p in _files:
    smp=str(p)
    xls=next((q for q in [p.with_suffix(s) for s in ('.XLS','.xls')] if q.exists()), None)
    if xls is None: continue
    rep=report(xls); g=lambda *ks: next((rep[k] for k in ks if k in rep), None)
    row={'file':p.name}
    try:
        r=load_file(p)
    except Exception as e:
        row['error']=repr(e); rows.append(row); continue
    row['model']=r.method_options.get('instrument_model')
    rc=r.run_conditions
    c={}
    c['mass']=rel(r.sample.sample_mass_g, num(g('sample mass:')), 5e-4)
    c['eq']=rel(rc.equilibration_interval_s, num(g('equilibration interval:')), 1e-6)
    try:
        bet=w._bet_analysis_for_result(r)
        c['bet']=rel(bet.surface_area_m2_g, num(g('bet surface area:')), 1e-4)
        c['C']=rel(bet.c_constant, num(g('c:')), 1e-3)
    except Exception as e: c['bet']='ERR '+repr(e)[:60]
    try:
        t,src=single_point_target(r,0.2); sp=single_point_bet_analysis(r,t)
        c['sp']=rel(sp.surface_area_m2_g, rep.get('sp_area'), 1e-4); row['sp_src']=src; row['sp_x_rep']=rep.get('sp_x'); row['sp_x_used']=sp.pressure_min
    except Exception as e: c['sp']='ERR '+repr(e)[:60]
    try:
        la=w._langmuir_analysis_for_result(r); c['lang']=rel(la.surface_area_m2_g, num(g('langmuir surface area:')), 1e-4)
    except Exception as e: c['lang']='ERR '+repr(e)[:60]
    try:
        tp=w._t_plot_analysis_for_result(r)
        ext=tp.external_surface_area_m2_g
        c['t_ext']=rel(ext, num(g('t-plot external surface area:')), 1e-4)
        c['t_micro_area']=rel((bet.surface_area_m2_g-ext) if ext is not None and bet.surface_area_m2_g is not None else None, num(g('t-plot micropore area:')), 1e-3)
        c['t_micro_vol']=rel(getattr(tp,'micropore_volume_cm3_g',None), num(g('t-plot micropore volume:')), 1e-3)
    except Exception as e: c['tplot']='ERR '+repr(e)[:60]
    row['checks']=c
    rows.append(row)
if _args.output:
    _args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
for row in rows:
    c=row.get('checks',{})
    bad={k:v for k,v in c.items() if v not in ('ok','-')}
    print(f"{row['file'][:40]:40s} {str(row.get('model'))[:16]:16s} {'ALL OK' if not bad and 'error' not in row else (row.get('error') or bad)}")
w.close()
w.deleteLater()
QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
sys.stdout.flush()
os._exit(0)  # skip Qt teardown in offscreen mode
