from flask import Flask, request, jsonify, render_template
from pathlib import Path
import chess, chess.pgn, json, os
import base64, io
from scid_backend import find_bases, game_count as scid_game_count, list_games as scid_list_games, game_pgn as scid_game_pgn

ROOT = Path(__file__).resolve().parent
PGN_DIR = ROOT / 'data' / 'pgn'
SCID_DIR = Path(os.environ.get('CHESS_SCID_DIR', str(ROOT / 'scid-import'))).resolve()
REP_FILE = ROOT / 'repertoire' / 'repertoire.json'
TRAIN_FILE = ROOT / 'training' / 'queue.json'
BOOK_DIR = ROOT / 'data' / 'books'
BOOK_INDEX = BOOK_DIR / 'index.json'
for p in (PGN_DIR, SCID_DIR, REP_FILE.parent, TRAIN_FILE.parent, BOOK_DIR): p.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
# Las bases SCID pueden ocupar cientos de MB; el límite se deja amplio.
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024 * 1024

@app.get('/')
def index():
    return render_template('index.html')

def iter_pgn_games():
    for file in sorted(PGN_DIR.glob('*.pgn')):
        try:
            with file.open(encoding='utf-8', errors='replace') as stream:
                while True:
                    game = chess.pgn.read_game(stream)
                    if game is None:
                        break
                    yield game
        except OSError:
            continue

PIECES = {'K':'♔','Q':'♕','R':'♖','B':'♗','N':'♘','P':'♙','k':'♚','q':'♛','r':'♜','b':'♝','n':'♞','p':'♟'}



@app.get('/api/status')
def status():
    files = [p for p in SCID_DIR.rglob('*') if p.is_file()]
    by_base = {}
    for item in files:
        if item.suffix.lower() in {'.sn4', '.si4', '.sg4'}:
            by_base.setdefault((item.parent, item.stem), set()).add(item.suffix.lower())
    bases = find_bases(SCID_DIR) if SCID_DIR.is_dir() else []
    ready = bool(bases)
    try: scid_games = sum(scid_game_count(base) for base in bases)
    except Exception: scid_games = None
    return jsonify({'pgn_files':len(list(PGN_DIR.glob('*.pgn'))),
                    'scid_files':[str(p.relative_to(SCID_DIR)) for p in files],
                    'scid_ready':ready, 'scid_bases':len(bases), 'scid_games':scid_games})
@app.get('/api/games')
def list_games():
    """Devuelve una muestra inicial de partidas para la lista lateral."""
    out = []
    for base in find_bases(SCID_DIR):
        rel = str(base.relative_to(SCID_DIR)); token = base64.urlsafe_b64encode(rel.encode()).decode().rstrip('=')
        try:
            for row in scid_list_games(base, 1, max(0, 300-len(out))):
                if len(row) < 9: continue
                number, white, black, event, date, result, we, be, eco = row[:9]
                out.append({'id':f'scid:{token}:{number}', 'white':white or '?', 'black':black or '?', 'event':event or '?', 'date':date or '', 'result':result or '*', 'white_elo':we or '', 'black_elo':be or '', 'eco':eco or ''})
        except Exception: app.logger.exception('No se pudo leer la base SCID %s', rel)
        if len(out) >= 300: break
    for file in sorted(PGN_DIR.glob('*.pgn')):
        try:
            with file.open(encoding='utf-8', errors='replace') as stream:
                index = 0
                while len(out) < 300:
                    game = chess.pgn.read_game(stream)
                    if game is None:
                        break
                    h = game.headers
                    out.append({'id': f'{file.name}:{index}', 'white': h.get('White','?'),
                                'black': h.get('Black','?'), 'event': h.get('Event','?'),
                                'date': h.get('Date',''), 'result': h.get('Result','*')})
                    index += 1
            if len(out) >= 300:
                break
        except OSError:
            continue
    return jsonify(out)

@app.get('/api/game')
def get_game():
    game_id = request.args.get('id','')
    try:
        if game_id.startswith('scid:'):
            _, token, number = game_id.split(':', 2)
            rel = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)).decode()
            base = (SCID_DIR / rel).resolve()
            if not base.is_relative_to(SCID_DIR) or base not in find_bases(SCID_DIR): raise ValueError('Base SCID no válida')
            game = chess.pgn.read_game(io.StringIO(scid_game_pgn(base, int(number))))
            if game is None: return jsonify({'error':'Partida no encontrada'}),404
            board=game.board(); positions=[{'fen':board.fen(),'san':'','comment':game.comment or ''}]; sans=[]
            for node in game.mainline():
                san=board.san(node.move); sans.append(san); board.push(node.move); positions.append({'fen':board.fen(),'san':san,'comment':node.comment or ''})
            return jsonify({'headers':dict(game.headers),'positions':positions,'sans':sans,'variations':[]})
        filename, number = game_id.rsplit(':', 1)
        number = int(number)
        path = (PGN_DIR / Path(filename).name).resolve()
        if path.parent != PGN_DIR.resolve() or number < 0:
            raise ValueError('Identificador no válido')
        with path.open(encoding='utf-8', errors='replace') as stream:
            game = None
            for _ in range(number + 1):
                game = chess.pgn.read_game(stream)
                if game is None:
                    break
        if game is None:
            return jsonify({'error':'Partida no encontrada'}),404
        board = game.board()
        positions = [{'fen':board.fen(),'san':'','comment':game.comment or ''}]
        sans = []
        for node in game.mainline():
            san = board.san(node.move)
            sans.append(san)
            board.push(node.move)
            positions.append({'fen':board.fen(),'san':san,'comment':node.comment or ''})
        variations = []
        def walk(parent, path_moves):
            for variation in parent.variations[1:]:
                b = parent.board()
                san = b.san(variation.move)
                b.push(variation.move)
                variations.append({'parent_path':path_moves,'san':san,'comment':variation.comment or '', 'fen':b.fen()})
                walk(variation, path_moves + [san])
            if parent.variations:
                walk(parent.variations[0], path_moves + ([parent.variations[0].san()] if parent.parent else []))
        # Las variantes se obtienen recursivamente desde cada nodo principal.
        node = game
        path_moves = []
        while node.variations:
            main = node.variations[0]
            for variation in node.variations[1:]:
                b = node.board(); san = b.san(variation.move); b.push(variation.move)
                variations.append({'parent_path':path_moves[:], 'san':san,'comment':variation.comment or '', 'fen':b.fen()})
            path_moves.append(node.board().san(main.move))
            node = main
        return jsonify({'headers':dict(game.headers),'positions':positions,'sans':sans,'variations':variations})
    except (ValueError, OSError) as e:
        return jsonify({'error':str(e)}),400

@app.get('/api/repertoire')
def get_rep(): return jsonify(json.loads(REP_FILE.read_text()) if REP_FILE.exists() else [])
from extensions import register as register_extensions
register_extensions(app, ROOT, PGN_DIR, REP_FILE)

if __name__=='__main__': app.run(host='0.0.0.0',port=8765,debug=False)
