"""API and regression tests. Run with: python3 -m unittest discover -s tests -v"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1]


class ChessLabAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='chesslab-tests-')
        cls.root = Path(cls.temp.name) / 'ChessLab'
        shutil.copytree(SOURCE, cls.root, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
        sys.path.insert(0, str(cls.root))
        # Keep tests isolated from the host SCID mount configured by Podman.
        cls.old_scid_dir = os.environ.pop('CHESS_SCID_DIR', None)
        spec = importlib.util.spec_from_file_location('chesslab_test_app', cls.root / 'app.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.client = cls.module.app.test_client()
        cls.old_scid = os.environ.get('CHESS_TCSCID_PATH')

    @classmethod
    def tearDownClass(cls):
        if cls.old_scid is None:
            os.environ.pop('CHESS_TCSCID_PATH', None)
        else:
            os.environ['CHESS_TCSCID_PATH'] = cls.old_scid
        if cls.old_scid_dir is None:
            os.environ.pop('CHESS_SCID_DIR', None)
        else:
            os.environ['CHESS_SCID_DIR'] = cls.old_scid_dir
        cls.temp.cleanup()

    def setUp(self):
        for folder in (self.module.PGN_DIR, self.module.SCId_DIR if hasattr(self.module, 'SCId_DIR') else self.module.SCID_DIR,
                       self.module.REP_FILE.parent, self.module.ROOT / 'repertoire'):
            folder.mkdir(parents=True, exist_ok=True)
        for p in self.module.PGN_DIR.glob('*.pgn'):
            p.unlink()
        for p in self.module.SCID_DIR.rglob('*'):
            if p.is_file(): p.unlink()
        for p in (self.module.REP_FILE, self.module.ROOT / 'repertoire' / 'variants.json'):
            p.unlink(missing_ok=True)
        os.environ.pop('CHESS_TCSCID_PATH', None)

    def _pgn(self):
        (self.module.PGN_DIR / 'sample.pgn').write_text(
            '[Event "Test Open"]\n[Site "Test"]\n[Date "2024.01.01"]\n'
            '[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n'
            '[WhiteElo "2100"]\n[BlackElo "2050"]\n[ECO "C00"]\n\n'
            '1. e4 e6 2. d4 d5 3. e5 c5 1-0\n', encoding='utf-8')

    def test_status_and_scid_detection_recurses(self):
        folder = self.module.SCID_DIR / 'nested' / 'Lumbras'
        folder.mkdir(parents=True)
        for ext in ('.sn4', '.si4', '.sg4'):
            (folder / ('Lumbras' + ext)).write_bytes(b'test')
        response = self.client.get('/api/status')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['scid_ready'])
        self.assertEqual(len(response.json['scid_files']), 3)

    def test_otb_games_and_position_statistics(self):
        self._pgn()
        games = self.client.get('/api/otb/games?player=alice&year=2024&tournament=Test').json
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]['white'], 'Alice')
        start_fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
        stats = self.client.get('/api/otb/stats', query_string={'fen': start_fen, 'min_elo': 2000}).json
        self.assertEqual(stats['games'], 1)
        self.assertEqual(stats['moves'][0]['move'], 'e4')
        filtered = self.client.get('/api/otb/games?min_elo=2200').json
        self.assertEqual(len(filtered), 0)

    def test_game_replay_endpoint(self):
        self._pgn()
        response = self.client.get('/api/game?id=sample.pgn:0')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['sans'][:2], ['e4', 'e6'])
        self.assertEqual(len(response.json['positions']), 7)

    def test_variant_crud_and_position_assignment(self):
        created = self.client.post('/api/variants', json={'name': 'Advance', 'color': 'Negras', 'opening': 'French'} )
        self.assertEqual(created.status_code, 201)
        variant_id = created.json['id']
        pos = self.client.post('/api/repertoire', json={
            'variant_id': variant_id, 'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
            'expected_san': 'e4', 'name': 'Initial position', 'line_name': 'Advance main', 'order': 1
        })
        self.assertEqual(pos.status_code, 201)
        self.assertEqual(self.client.get('/api/variants').json[0]['position_count'], 1)
        edited = self.client.patch('/api/variants/' + variant_id, json={'name': 'Advance updated'})
        self.assertEqual(edited.json['name'], 'Advance updated')
        deleted = self.client.delete('/api/variants/' + variant_id)
        self.assertEqual(deleted.json['positions_preserved'], 1)
        self.assertIsNone(self.client.get('/api/repertoire').json[0]['variant_id'])

    def test_repertoire_rejects_illegal_move_and_invalid_variant(self):
        bad = self.client.post('/api/repertoire', json={'fen': 'not-a-fen', 'expected_san': 'e5'})
        self.assertEqual(bad.status_code, 400)
        bad_variant = self.client.post('/api/repertoire', json={
            'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
            'expected_san': 'e4', 'variant_id': 'missing'
        })
        self.assertEqual(bad_variant.status_code, 404)

    def test_spaced_repetition_answer_stats_and_auto_next_queue(self):
        fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
        made = self.client.post('/api/repertoire', json={'fen': fen, 'expected_san': 'e4', 'name': 'Test move'}).json['item']
        due = self.client.get('/api/training/due').json
        self.assertEqual(len(due['due']), 1)
        wrong = self.client.post('/api/training/answer', json={'id': made['id'], 'answer': 'd4', 'grade': 4}).json
        self.assertFalse(wrong['correct'])
        self.assertEqual(wrong['interval'], 1)
        stats = self.client.get('/api/training/stats').json
        self.assertEqual(stats['incorrect'], 1)
        self.assertEqual(stats['correct'], 0)
        # Force due and check a correct answer is recorded.
        data = json.loads(self.module.REP_FILE.read_text())
        data[0]['due'] = '2000-01-01'
        self.module.REP_FILE.write_text(json.dumps(data))
        correct = self.client.post('/api/training/answer', json={'id': made['id'], 'answer': 'e4', 'grade': 4}).json
        self.assertTrue(correct['correct'])
        stats = self.client.get('/api/training/stats').json
        self.assertEqual(stats['reviews'], 2)
        self.assertEqual(stats['correct'], 1)
        self.assertEqual(stats['incorrect'], 1)
        self.assertEqual(stats['accuracy'], 50.0)

    def test_ui_contains_core_modules_and_repertoire_controls(self):
        html = (self.root / 'templates' / 'index.html').read_text(encoding='utf-8')
        for marker in ('data-tab=\"library\"', 'data-tab=\"repertoire\"', 'data-tab=\"analysis\"',
                       'starttraining', 'repmetrics', 'variantlist', 'Base SCID nativa', 'otbanalyze'):
            self.assertIn(marker, html)

    def test_stockfish_can_analyze_a_position_when_required(self):
        if os.environ.get('CHESSLAB_REQUIRE_STOCKFISH') != '1':
            self.skipTest('La prueba real del motor se activa dentro del contenedor.')
        response = self.client.post('/api/engine/analyze', json={
            'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
            'depth': 8
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertGreaterEqual(len(response.json['lines']), 1)
        self.assertTrue(response.json['lines'][0]['moves'])

    def test_pgn_import_endpoint_adds_games_to_database(self):
        from io import BytesIO
        response = self.client.post('/api/otb/import', data={
            'files': (BytesIO(b'[Event "Imported"]\n[White "One"]\n[Black "Two"]\n[Result "*"]\n\n1. d4 *\n'), 'imported.pgn')
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['total_games'], 1)
        self.assertEqual(self.client.get('/api/games').json[0]['white'], 'One')

    def test_stockfish_status_reports_unavailable_cleanly(self):
        response = self.client.get('/api/engine/status')
        self.assertEqual(response.status_code, 200)
        self.assertIn('available', response.json)
        if os.environ.get('CHESSLAB_REQUIRE_STOCKFISH') == '1':
            self.assertTrue(response.json['available'], response.json)
            self.assertIn('Stockfish', response.json['name'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
