# ChessLab

ChessLab is a local chess study app. It reads Scid databases directly, explores opening move frequencies, and analyzes the current position with Stockfish 19.

## Run

Requirements: Podman and an internet connection for the first image build.

1. Put the three files for each Scid database (`name.si4`, `name.sg4`, `name.sn4`) together in `scid-import/`.
2. Run `./podman-run.sh`.
3. Open <http://127.0.0.1:8765>.

The container builds Scid vs. PC 4.27's `tcscid` console reader and downloads the official Stockfish 19 Linux universal binary. Both downloads are pinned by SHA-256. Later starts reuse the built image.

## Opening explorer

The opening explorer starts at the initial position. Click a move to see its continuations and game counts. The most likely continuation is prefetched; visited positions are cached in the browser. Stockfish analyzes the current board automatically while the opening tree loads.

## Local data

The Scid database is mounted read-only. ChessLab only reads it; it never exports the whole database. Imported PGNs, repertoire, and training data stay in `data/`, `repertoire/`, and `training/` and are not tracked by Git.

The repository does not include any Scid database, PGN collection, repertoire, or training history. Place your own data locally in the folders above.

## Commands

```bash
./podman-run.sh          # build/rebuild and start ChessLab
podman logs -f chesslab  # view logs
podman stop chesslab     # stop the app
./test-container.sh      # run the isolated tests
```

The app is bound to `127.0.0.1:8765` only.
