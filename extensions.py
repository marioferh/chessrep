from pathlib import Path
from datetime import date, timedelta, datetime
import json, uuid, shutil, os
import base64
import chess, chess.pgn, chess.engine
from flask import request, jsonify, Response, stream_with_context
from scid_backend import find_bases, position_stats as scid_position_stats, list_games as scid_list_games

def register(app, root, pgn_dir, rep_file):
    root=Path(root); pgn_dir=Path(pgn_dir); rep_file=Path(rep_file)
    meta_file=root/'data'/'otb_imports.json'; variants_file=root/'repertoire'/'variants.json'
    def reps():
        try: return json.loads(rep_file.read_text(encoding='utf-8'))
        except Exception: return []
    def save(items): rep_file.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
    def variants():
        try: return json.loads(variants_file.read_text(encoding='utf-8'))
        except Exception: return []
    def save_variants(items): variants_file.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
    def games():
        for f in sorted(pgn_dir.glob('*.pgn')):
            try:
                with f.open(encoding='utf-8',errors='replace') as h:
                    i=0
                    while True:
                        g=chess.pgn.read_game(h)
                        if g is None: break
                        yield f,i,g; i+=1
            except Exception: continue
    def poskey(b): return ' '.join(b.fen().split()[:4])

    @app.post('/api/otb/import')
    def otb_import():
        saved=[]
        for f in request.files.getlist('files'):
            name=Path(f.filename or '').name
            if not name.lower().endswith('.pgn'): continue
            target=pgn_dir/name
            if target.exists(): target=pgn_dir/f'{target.stem}_{uuid.uuid4().hex[:6]}.pgn'
            f.save(target); saved.append(target.name)
        old=[]
        try: old=json.loads(meta_file.read_text())
        except Exception: pass
        old += [{'file':n,'source':'OTB','imported':datetime.now().isoformat(timespec='seconds')} for n in saved]
        meta_file.write_text(json.dumps(old,indent=2),encoding='utf-8')
        return jsonify({'saved':saved,'total_games':sum(1 for _ in games())})

    @app.post('/api/otb/explorer/move')
    def otb_explorer_move():
        data=request.get_json(force=True)
        try:
            board=chess.Board(data.get('fen') or chess.STARTING_FEN)
            move=board.parse_san((data.get('san') or '').strip())
            san=board.san(move)
            board.push(move)
            return jsonify({'fen':board.fen(),'san':san})
        except (ValueError, TypeError):
            return jsonify({'error':'Jugada no válida para esta posición.'}),400

    @app.get('/api/otb/stats')
    def otb_stats():
        q=request.args; player=q.get('player','').lower().strip(); year=q.get('year','').strip(); event=q.get('tournament','').lower().strip(); elo=int(q.get('min_elo') or 0)
        target=chess.Board(q.get('fen')) if q.get('fen') else chess.Board(); key=poskey(target); counts={}; found=0
        scid_root=Path(os.environ.get('CHESS_SCID_DIR', str(Path(root)/'scid-import'))).resolve()
        bases=find_bases(scid_root)
        if bases:
            filters={'player':q.get('player',''),'year':year,'tournament':q.get('tournament',''),'min_elo':elo}
            try:
                native=scid_position_stats(bases[0],target.fen(),filters)
                return jsonify(native)
            except Exception as exc:
                app.logger.exception('Error consultando estadísticas SCID')
                return jsonify({'error':f'No se pudo consultar la base SCID: {exc}'}),503
        for f,i,g in games():
            h=g.headers
            if player and player not in (h.get('White','').lower()+' '+h.get('Black','').lower()): continue
            if year and not h.get('Date','').startswith(year): continue
            if event and event not in h.get('Event','').lower(): continue
            try:
                if elo and min(int(h.get('WhiteElo','0') or 0),int(h.get('BlackElo','0') or 0))<elo: continue
            except ValueError: continue
            b=g.board()
            for node in g.mainline():
                if poskey(b)==key:
                    found+=1; san=b.san(node.move); r=h.get('Result','*')
                    x=counts.setdefault(san,{'move':san,'games':0,'white':0,'draw':0,'black':0}); x['games']+=1
                    if r=='1-0': x['white']+=1
                    elif r=='0-1': x['black']+=1
                    elif r=='1/2-1/2': x['draw']+=1
                    break
                b.push(node.move)
            else:
                if poskey(b)==key and not g.end().parent.variations: found+=1
        rows=sorted(counts.values(),key=lambda x:-x['games'])
        return jsonify({'position':key,'games':sum(x['games'] for x in rows),'positions_found':found,'moves':rows})

    @app.get('/api/otb/games')
    def otb_games():
        q=request.args; player=q.get('player','').lower(); year=q.get('year',''); event=q.get('tournament','').lower(); out=[]
        try: min_elo=int(q.get('min_elo') or 0)
        except ValueError: min_elo=0
        scid_root=Path(os.environ.get('CHESS_SCID_DIR', str(Path(root)/'scid-import'))).resolve()
        bases=find_bases(scid_root)
        if bases:
            try:
                for row in scid_list_games(bases[0],1,300,{'player':player,'year':year,'tournament':event,'min_elo':min_elo}):
                    if len(row)<9: continue
                    n,w,b,e,d,r,we,be,eco=row[:9]
                    token=base64.urlsafe_b64encode(str(bases[0].relative_to(scid_root)).encode()).decode().rstrip('=')
                    out.append({'id':f'scid:{token}:{n}','white':w,'black':b,'event':e,'date':d,'result':r,'white_elo':we,'black_elo':be,'eco':eco})
                return jsonify(out)
            except Exception as exc:
                return jsonify({'error':f'No se pudo consultar la base SCID: {exc}'}),503
        for f,i,g in games():
            h=g.headers
            if player and player not in (h.get('White','').lower()+' '+h.get('Black','').lower()): continue
            if year and not h.get('Date','').startswith(year): continue
            if event and event not in h.get('Event','').lower(): continue
            if min_elo:
                try:
                    if min(int(h.get('WhiteElo','0') or 0), int(h.get('BlackElo','0') or 0)) < min_elo: continue
                except ValueError: continue
            out.append({'id':f'{f.name}:{i}','white':h.get('White','?'),'black':h.get('Black','?'),'event':h.get('Event','?'),'date':h.get('Date',''),'result':h.get('Result','*'),'white_elo':h.get('WhiteElo',''),'black_elo':h.get('BlackElo',''),'eco':h.get('ECO',''),'opening':h.get('Opening','')})
            if len(out)>=300: break
        return jsonify(out)

    @app.get('/api/variants')
    def variants_list():
        items=variants(); positions=reps()
        for v in items:
            v['position_count']=sum(1 for x in positions if x.get('variant_id')==v['id'])
        return jsonify(items)

    @app.post('/api/variants')
    def variant_create():
        d=request.get_json(force=True); name=(d.get('name') or '').strip()
        if not name: return jsonify({'error':'Indica el nombre de la variante o línea.'}),400
        v={'id':uuid.uuid4().hex,'name':name,'opening':(d.get('opening') or '').strip(),'color':d.get('color','Negras'),'note':(d.get('note') or '').strip(),'created':date.today().isoformat()}
        a=variants(); a.append(v); save_variants(a); return jsonify(v),201

    @app.patch('/api/variants/<variant_id>')
    def variant_update(variant_id):
        d=request.get_json(force=True); a=variants(); v=next((x for x in a if x['id']==variant_id),None)
        if not v: return jsonify({'error':'Variante no encontrada'}),404
        for k in ('name','opening','color','note'):
            if k in d: v[k]=d[k]
        save_variants(a); return jsonify(v)

    @app.delete('/api/variants/<variant_id>')
    def variant_delete(variant_id):
        a=variants(); kept=[x for x in a if x['id']!=variant_id]
        if len(a)==len(kept): return jsonify({'error':'Variante no encontrada'}),404
        # No se pierden posiciones: quedan en una sección sin variante asignada.
        positions=reps(); preserved=0
        for item in positions:
            if item.get('variant_id')==variant_id: item['variant_id']=None; preserved+=1
        save(positions); save_variants(kept); return jsonify({'deleted':True,'positions_preserved':preserved})

    @app.post('/api/repertoire')
    def rep_add():
        d=request.get_json(force=True)
        try: b=chess.Board(d.get('fen',''))
        except Exception: return jsonify({'error':'FEN no válida'}),400
        san=(d.get('expected_san') or '').strip()
        if not san: return jsonify({'error':'Indica la jugada esperada en SAN.'}),400
        try: san=b.san(b.parse_san(san))
        except ValueError: return jsonify({'error':'Jugada esperada ilegal en esta posición'}),400
        variant_id=d.get('variant_id') or None
        if variant_id and not any(v['id']==variant_id for v in variants()): return jsonify({'error':'Variante no encontrada'}),404
        x={'id':uuid.uuid4().hex,'variant_id':variant_id,'line_name':(d.get('line_name') or '').strip(),'order':int(d.get('order') or 0),'color':d.get('color','Negras'),'name':d.get('name','Sin nombre'),'note':d.get('note',''),'fen':b.fen(),'expected_san':san,'due':date.today().isoformat(),'interval':0,'ease':2.5,'reps':0,'lapses':0,'correct_count':0,'incorrect_count':0,'review_count':0,'last_review':None,'last_grade':None}
        a=reps(); a.append(x); save(a); return jsonify({'item':x,'count':len(a)}),201

    @app.post('/api/repertoire/line')
    def rep_add_line():
        d=request.get_json(force=True); variant_id=d.get('variant_id'); line_name=(d.get('line_name') or 'Línea Stockfish').strip()
        moves=d.get('moves',[]); color=d.get('color','Negras')
        variant=next((v for v in variants() if v['id']==variant_id),None)
        if not variant: return jsonify({'error':'Selecciona un árbol de variantes válido.'}),404
        if color not in ('Blancas','Negras'): return jsonify({'error':'El color debe ser Blancas o Negras.'}),400
        if not isinstance(moves,list) or not moves or len(moves)>24: return jsonify({'error':'La variante debe contener entre 1 y 24 jugadas.'}),400
        try:
            board=chess.Board(d.get('fen','')); side=chess.WHITE if color=='Blancas' else chess.BLACK
            existing=reps(); added=[]
            for index,raw_san in enumerate(moves):
                move=board.parse_san(str(raw_san)); san=board.san(move)
                if board.turn==side:
                    added.append({'id':uuid.uuid4().hex,'variant_id':variant_id,'line_name':line_name,'order':index,'color':color,'name':line_name,'note':'Variante guardada desde el análisis de Stockfish.','fen':board.fen(),'expected_san':san,'due':date.today().isoformat(),'interval':0,'ease':2.5,'reps':0,'lapses':0,'correct_count':0,'incorrect_count':0,'review_count':0,'last_review':None,'last_grade':None})
                board.push(move)
            if not added: return jsonify({'error':f'La línea no contiene jugadas para el repertorio de {color.lower()}.'}),400
            existing.extend(added); save(existing)
            return jsonify({'saved':len(added),'variant':variant['name'],'line_name':line_name,'items':added}),201
        except Exception as e: return jsonify({'error':str(e)}),400

    @app.patch('/api/repertoire/<item_id>')
    def rep_update(item_id):
        d=request.get_json(force=True); a=reps(); x=next((z for z in a if z['id']==item_id),None)
        if not x: return jsonify({'error':'Posición no encontrada'}),404
        if 'variant_id' in d and d['variant_id'] and not any(v['id']==d['variant_id'] for v in variants()):
            return jsonify({'error':'Variante no encontrada'}),404
        for k in ('name','note','line_name','color','order','variant_id'):
            if k in d: x[k]=d[k]
        if 'fen' in d:
            try: x['fen']=chess.Board(d['fen']).fen()
            except Exception: return jsonify({'error':'FEN no válida'}),400
        if 'expected_san' in d:
            b=chess.Board(x['fen'])
            try: x['expected_san']=b.san(b.parse_san(d['expected_san']))
            except ValueError: return jsonify({'error':'Jugada esperada ilegal en esta posición'}),400
        save(a); return jsonify(x)

    @app.delete('/api/repertoire/<item_id>')
    def rep_delete(item_id):
        a=reps(); kept=[x for x in a if x['id']!=item_id]
        if len(a)==len(kept): return jsonify({'error':'Posición no encontrada'}),404
        save(kept); return jsonify({'deleted':True})

    @app.get('/api/training/due')
    def training_due():
        a=reps(); today=date.today().isoformat(); due=[x for x in a if x.get('expected_san') and x.get('due','')<=today]
        due.sort(key=lambda x:(x.get('due',''),x.get('lapses',0)*-1,x.get('order',0)))
        return jsonify({'due':due,'total':len([x for x in a if x.get('expected_san')]),'all':len(a)})

    @app.get('/api/training/stats')
    def training_stats():
        a=reps(); practiced=[x for x in a if x.get('review_count',0)>0]
        reviews=sum(x.get('review_count',0) for x in a); correct=sum(x.get('correct_count',0) for x in a); incorrect=sum(x.get('incorrect_count',0) for x in a)
        today=date.today().isoformat(); due=sum(1 for x in a if x.get('expected_san') and x.get('due','')<=today)
        return jsonify({'positions':len(a),'active_positions':sum(1 for x in a if x.get('expected_san')),'reviewed_positions':len(practiced),'reviews':reviews,'correct':correct,'incorrect':incorrect,'accuracy':round(100*correct/reviews,1) if reviews else 0,'due':due,'forgotten':sum(1 for x in a if x.get('lapses',0)>=2),'lapses':sum(x.get('lapses',0) for x in a)})

    @app.post('/api/training/answer')
    def training_answer():
        d=request.get_json(force=True); a=reps(); x=next((z for z in a if z['id']==d.get('id')),None)
        if not x: return jsonify({'error':'Posición no encontrada'}),404
        b=chess.Board(x['fen']); ans=(d.get('answer') or '').strip()
        try: normalized=b.san(b.parse_san(ans)); correct=normalized==x['expected_san']
        except ValueError: normalized=ans; correct=False
        # 0=fallo, 3=difícil, 4=correcto, 5=fácil. Un fallo reinicia el intervalo.
        grade=max(0,min(5,int(d.get('grade',4 if correct else 0))))
        if not correct: grade=min(grade,2)
        if grade<3:
            x['reps']=0; x['interval']=1; x['lapses']=x.get('lapses',0)+1
        else:
            if x.get('reps',0)==0: x['interval']=1
            elif x.get('reps',0)==1: x['interval']=6
            else: x['interval']=max(1,round(x.get('interval',1)*x.get('ease',2.5)*(1.3 if grade==5 else 1.0 if grade==4 else 0.8)))
            x['reps']=x.get('reps',0)+1
        x['ease']=max(1.3,x.get('ease',2.5)+(0.1-(5-grade)*(0.08+(5-grade)*0.02)))
        x['review_count']=x.get('review_count',0)+1
        if correct: x['correct_count']=x.get('correct_count',0)+1
        else: x['incorrect_count']=x.get('incorrect_count',0)+1
        x['due']=(date.today()+timedelta(days=x['interval'])).isoformat(); x['last_correct']=correct; x['last_answer']=normalized; x['last_review']=date.today().isoformat(); x['last_grade']=grade
        save(a); return jsonify({'correct':correct,'expected':x['expected_san'],'note':x['note'],'due':x['due'],'interval':x['interval'],'grade':grade,'correct_count':x['correct_count'],'incorrect_count':x['incorrect_count']})

    def stockfish_path():
        return os.environ.get('CHESS_STOCKFISH_PATH') or shutil.which('stockfish') or '/usr/games/stockfish'

    @app.get('/api/engine/status')
    def engine_status():
        p=stockfish_path()
        if not p or not Path(p).is_file() or not os.access(p, os.X_OK):
            return jsonify({'available':False,'path':p,'hint':'Stockfish no está instalado dentro del contenedor. Reconstruye la imagen con ./podman-run.sh.'})
        try:
            with chess.engine.SimpleEngine.popen_uci(p) as engine:
                identity=engine.id
            return jsonify({'available':True,'path':p,'name':identity.get('name','Stockfish'),'author':identity.get('author',''),'hint':'Motor UCI iniciado correctamente dentro del contenedor.'})
        except Exception as exc:
            return jsonify({'available':False,'path':p,'hint':f'El ejecutable existe, pero no inicia correctamente: {exc}'})

    @app.post('/api/engine/analyze')
    def engine_analyze():
        try:
            d=request.get_json(force=True); b=chess.Board(d.get('fen','')); p=stockfish_path()
            if not p or not Path(p).is_file() or not os.access(p, os.X_OK):
                return jsonify({'error':'No se encuentra Stockfish dentro del contenedor. Reconstruye la imagen con ./podman-run.sh.'}),503
            depth=max(8,min(30,int(d.get('depth',16))))
            with chess.engine.SimpleEngine.popen_uci(p) as e:
                e.configure({'Threads': max(1, min(4, os.cpu_count() or 1)), 'Hash': 256})
                infos=e.analyse(b,chess.engine.Limit(depth=depth),multipv=3)
            if isinstance(infos, dict): infos=[infos]
            out=[]
            for info in infos:
                board=b.copy(); sans=[]
                for m in info.get('pv',[])[:12]: sans.append(board.san(m)); board.push(m)
                score=info['score'].pov(chess.WHITE)
                out.append({'cp':score.score(mate_score=100000),'mate':score.mate(),'moves':sans})
            return jsonify({'depth':depth,'lines':out,'engine':'Stockfish'})
        except Exception as e: return jsonify({'error':str(e)}),400

    @app.post('/api/engine/stream')
    def engine_stream():
        try:
            d=request.get_json(force=True); b=chess.Board(d.get('fen','')); p=stockfish_path()
            if not p or not Path(p).is_file() or not os.access(p, os.X_OK):
                return jsonify({'error':'No se encuentra Stockfish dentro del contenedor. Reconstruye la imagen con ./podman-run.sh.'}),503
            depth=max(8,min(30,int(d.get('depth',12))))
        except Exception as e:
            return jsonify({'error':str(e)}),400

        def events():
            try:
                with chess.engine.SimpleEngine.popen_uci(p) as engine:
                    engine.configure({'Threads': max(1, min(4, os.cpu_count() or 1)), 'Hash': 256})
                    lines={}
                    with engine.analysis(b,chess.engine.Limit(depth=depth),multipv=3) as analysis:
                        for info in analysis:
                            index=info.get('multipv',1)-1
                            board=b.copy(); sans=[]
                            for move in info.get('pv',[])[:12]:
                                sans.append(board.san(move)); board.push(move)
                            if not sans or 'score' not in info: continue
                            score=info['score'].pov(chess.WHITE)
                            lines[index]={'cp':score.score(mate_score=100000),'mate':score.mate(),'moves':sans}
                            yield 'data: '+json.dumps({'type':'progress','depth':info.get('depth',0),'seldepth':info.get('seldepth',0),'nodes':info.get('nodes',0),'nps':info.get('nps',0),'lines':[lines[i] for i in sorted(lines)]})+'\n\n'
                yield 'data: '+json.dumps({'type':'done','depth':depth})+'\n\n'
            except Exception as e:
                yield 'data: '+json.dumps({'type':'error','error':str(e)})+'\n\n'
        return Response(stream_with_context(events()),mimetype='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    @app.post('/api/engine/line')
    def engine_line():
        try:
            d=request.get_json(force=True); board=chess.Board(d.get('fen','')); positions=[{'fen':board.fen(),'san':''}]
            moves=d.get('moves',[])
            if not isinstance(moves,list) or len(moves)>24: raise ValueError('La variante no es válida.')
            for san in moves:
                move=board.parse_san(str(san)); san=board.san(move); board.push(move)
                positions.append({'fen':board.fen(),'san':san})
            return jsonify({'positions':positions})
        except Exception as e: return jsonify({'error':str(e)}),400
