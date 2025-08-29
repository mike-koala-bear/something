import fs from "fs"
import { spawn } from "child_process"
import { Chess } from "chess.js"

const ENGINE_PATH = "/opt/homebrew/bin/stockfish" // adjust path if needed
const BOOK_FILE = "./book.json"

const DEPTH = 20
const MAX_PLIES = 16
const MOVES = 1
const SAVE_INTERVAL = 100 // save after this many positions

let book = {}
if (fs.existsSync(BOOK_FILE)) {
  console.log("♻️  Resuming from existing book.json...")
  book = JSON.parse(fs.readFileSync(BOOK_FILE, "utf-8"))
}

function initEngine() {
  const engine = spawn(ENGINE_PATH)
  engine.stdin.setDefaultEncoding("utf-8")

  function send(cmd) {
    engine.stdin.write(cmd + "\n")
  }

  function go(fen) {
    return new Promise((resolve) => {
      let bestmove = null
      const listener = (data) => {
        const line = data.toString().trim()
        if (line.startsWith("bestmove")) {
          bestmove = line.split(" ")[1]
          engine.stdout.off("data", listener)
          resolve(bestmove)
        }
      }
      engine.stdout.on("data", listener)

      send("ucinewgame")
      send(`position fen ${fen}`)
      send(`go depth ${DEPTH}`)
    })
  }

  return { go, send }
}

const engine = initEngine()

async function analyze(fen, ply = 0) {
  if (ply >= MAX_PLIES) return

  if (book[fen]) return // already analyzed

  const move = await engine.go(fen)
  if (!move) return

  book[fen] = { move }

  const chess = new Chess(fen)
  chess.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: "q" })
  await analyze(chess.fen(), ply + 1)
}

let counter = 0
async function build() {
  console.log(`📘 Building opening book (plies=${MAX_PLIES}, depth=${DEPTH})`)
  const startFen = new Chess().fen()
  await analyze(startFen, 0)

  counter++
  if (counter % SAVE_INTERVAL === 0) {
    fs.writeFileSync(BOOK_FILE, JSON.stringify(book, null, 2))
    console.log(`💾 Saved checkpoint: ${Object.keys(book).length} positions`)
  }

  // final save
  fs.writeFileSync(BOOK_FILE, JSON.stringify(book, null, 2))
  console.log(
    `✅ Finished. Saved ${Object.keys(book).length} positions to ${BOOK_FILE}`
  )
}

build()
