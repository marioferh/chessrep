from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import threading
from pathlib import Path


TCSCID = os.environ.get('CHESS_TCSCID_PATH', '/usr/local/bin/tcscid')
_sessions = {}
_sessions_lock = threading.Lock()


def find_bases(root: Path):
    root = Path(root).resolve()
    found = []
    for si4 in root.rglob('*.si4'):
        base = si4.with_suffix('')
        if base.with_suffix('.sn4').is_file() and base.with_suffix('.sg4').is_file():
            found.append(base)
    return sorted(found)


def _tcl_brace(value):
    value = str(value).replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}')
    return '{' + value + '}'


def _server_script(base):
    return f'''set slot [sc_base open -readonly {_tcl_brace(base)}]
if {{![string is integer -strict $slot]}} {{puts "@ERROR:$slot"; flush stdout; exit 2}}
puts "@READY"
flush stdout
proc emit {{fields}} {{
    set encoded {{}}
    foreach value $fields {{
        lappend encoded [binary encode base64 -maxlen 0 [encoding convertto utf-8 $value]]
    }}
    puts "@ROW:[join $encoded \\t]"
    flush stdout
}}
while {{[gets stdin line] >= 0}} {{
    if {{$line eq "@QUIT"}} break
    set parts [split $line \\t]
    set requestId [lindex $parts 0]
    set command [encoding convertfrom utf-8 [binary decode base64 [lindex $parts 1]]]
    set code [catch {{uplevel #0 $command}} result]
    if {{$code}} {{
        puts "@FAIL:$requestId:[binary encode base64 -maxlen 0 [encoding convertto utf-8 $result]]"
    }} else {{
        puts "@DONE:$requestId"
    }}
    flush stdout
}}
exit 0
'''


class _Session:
    def __init__(self, base):
        self.base = Path(base).resolve()
        self.lock = threading.Lock()
        self.counter = 0
        self.script = tempfile.NamedTemporaryFile('w', suffix='.tcl', encoding='utf-8', delete=False)
        self.script.write(_server_script(self.base))
        self.script.close()
        self.proc = subprocess.Popen(
            [TCSCID, self.script.name], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1
        )
        ready = self.proc.stdout.readline().rstrip('\n')
        if ready != '@READY':
            self.close()
            raise RuntimeError(f'tcscid no pudo abrir {self.base}: {ready}')

    def request(self, script):
        with self.lock:
            if self.proc.poll() is not None:
                raise RuntimeError('El proceso tcscid terminó inesperadamente.')
            self.counter += 1
            request_id = str(self.counter)
            payload = base64.b64encode(script.encode('utf-8')).decode('ascii')
            self.proc.stdin.write(f'{request_id}\t{payload}\n')
            self.proc.stdin.flush()
            rows = []
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError('tcscid cerró la conexión.')
                line = line.rstrip('\n')
                if line == f'@DONE:{request_id}':
                    return rows
                if line.startswith(f'@FAIL:{request_id}:'):
                    message = base64.b64decode(line.split(':', 2)[2]).decode('utf-8', 'replace')
                    raise RuntimeError(message)
                if line.startswith('@ROW:'):
                    rows.append([
                        base64.b64decode(cell).decode('utf-8', 'replace')
                        for cell in line[5:].split('\t')
                    ])

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.stdin.write('@QUIT\n')
                self.proc.stdin.flush()
                self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()
        try:
            os.unlink(self.script.name)
        except OSError:
            pass


def _session(base):
    key = str(Path(base).resolve())
    with _sessions_lock:
        session = _sessions.get(key)
        if session is None or session.proc.poll() is not None:
            session = _Session(key)
            _sessions[key] = session
        return session


def _quote_search(value):
    # Scid treats a double-quoted value as a wildcard search.
    value = str(value).replace('\\', '\\\\').replace('"', '\\"')
    return _tcl_brace('"*' + value + '*"')


def _filters(filters):
    lines = ['sc_filter reset $slot']
    operation = 2
    player = (filters.get('player') or '').strip()
    if player:
        pattern = _quote_search(player)
        lines.append(f'sc_search header -filter {operation} -white {pattern}')
        lines.append(f'sc_search header -filter 1 -black {pattern}')
        operation = 0
    year = str(filters.get('year') or '').strip()
    if year.isdigit() and len(year) == 4:
        lines.append(f'sc_search header -filter {operation} -date {_tcl_brace(year + ".01.01 " + year + ".12.31")}')
        operation = 0
    event = (filters.get('tournament') or '').strip()
    if event:
        lines.append(f'sc_search header -filter {operation} -event {_quote_search(event)}')
        operation = 0
    try:
        elo = max(0, int(filters.get('min_elo') or 0))
    except (TypeError, ValueError):
        elo = 0
    if elo:
        lines.append(f'sc_search header -filter {operation} -welo {_tcl_brace(f"{elo} 9999")} -belo {_tcl_brace(f"{elo} 9999")}')
    return lines


def game_count(base):
    rows = _session(base).request('emit [list [sc_base numGames $slot]]')
    return int(rows[0][0])


def list_games(base, start=1, limit=300, filters=None):
    session = _session(base)
    lines = _filters(filters or {})
    lines += [
        f'set count [sc_filter count $slot]',
        f'set end [expr {{min($count, {int(limit)})}}]',
        f'set ids [sc_game list {int(start)} $end g]',
        'foreach id $ids {',
        '    if {[sc_game load $id] eq ""} {',
        '        emit [list $id [sc_game tags get White] [sc_game tags get Black] [sc_game tags get Event] [sc_game tags get Date] [sc_game tags get Result] [sc_game tags get WhiteElo] [sc_game tags get BlackElo] [sc_game tags get ECO]]',
        '    }',
        '}',
    ]
    return session.request('\n'.join(lines))


def game_pgn(base, game_number):
    rows = _session(base).request(
        f'if {{[sc_game load {int(game_number)}] eq ""}} {{emit [list [sc_game pgn -comments 1 -variations 1]]}}'
    )
    if not rows:
        raise LookupError('Partida SCID no encontrada.')
    return rows[0][0]


def position_stats(base, fen, filters=None):
    lines = _filters(filters or {})
    lines += [
        f'sc_game new',
        f'set boardResult [sc_game startBoard {_tcl_brace(fen)}]',
        'if {$boardResult ne ""} {error $boardResult}',
        'set tree [sc_tree search -list 1 -fastmode 0 -adjust 0 -sort frequency]',
        'foreach row $tree {',
        '    set fields [split $row "\\t"]',
        '    emit [lrange $fields end-5 end]',
        '}',
    ]
    rows = _session(base).request('\n'.join(lines))
    result_moves = []
    games = 0
    total_moves = 0
    for row in rows:
        if len(row) != 6:
            continue
        move, count, white, draw, black, undecided = row
        count = int(count)
        if move == '[end]':
            games = count
            continue
        total_moves += count
        result_moves.append({'move': move, 'games': count, 'white': int(white), 'draw': int(draw), 'black': int(black)})
    if games == 0:
        games = total_moves
    return {'position': ' '.join(fen.split()[:4]), 'games': games,
            'positions_found': games, 'moves': result_moves}
