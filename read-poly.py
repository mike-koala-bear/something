import chess
import chess.polyglot

book_path = "./books/staybook.bin"

with chess.polyglot.open_reader(book_path) as reader:
    for i, entry in enumerate(reader):
        print(f"Entry {i+1}:")
        print(f"  Move (UCI): {entry.move}")  # move as UCI
        print(f"  Weight: {entry.weight}")
        print(f"  Learn: {entry.learn}")
        print("-------------------------")
