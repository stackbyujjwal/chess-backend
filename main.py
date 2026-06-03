from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from stockfish import Stockfish
from pydantic import BaseModel
import threading
from typing import Dict, List

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

engine_lock = threading.Lock()

def create_engine():
    # Ensure path is strictly ./stockfish-linux for Render
    sf = Stockfish(path="./stockfish-linux")
    # Free Server Optimization: Strict 64MB RAM and 1 Thread
    sf.update_engine_parameters({"Hash": 64, "Threads": 1})
    return sf

stockfish = create_engine()

class Position(BaseModel):
    fen_string: str
    think_time: int

@app.post("/calculate_move")
def calculate_move(pos: Position):
    global stockfish
    with engine_lock:
        try:
            if not stockfish.is_fen_valid(pos.fen_string):
                return {"best_move": None, "score": "Invalid", "pv": "Impossible board position.", "depth": 0}

            stockfish.set_fen_position(pos.fen_string)
            
            # FREE SERVER OPTIMIZATION: 3 alag heavy calls ki jagah sirf 1 baar call karenge
            top_moves = stockfish.get_top_moves(1)
            
            if not top_moves:
                return {"best_move": None, "score": "0", "pv": "", "depth": 0}
                
            best_move = top_moves[0]["Move"]
            cp = top_moves[0].get("Centipawn")
            mate = top_moves[0].get("Mate")
            
            if mate is not None:
                score = f"Mate in {mate}"
            else:
                score = round(cp / 100, 2) if cp is not None else 0
                
            return {"best_move": best_move, "score": score, "pv": best_move, "depth": 15}
            
        except Exception as e:
            # Agar free server fir bhi limit hit kare aur crash ho, toh engine auto-restart ho jayega
            stockfish = create_engine()
            return {"best_move": None, "score": "Error", "pv": "Engine rebooted.", "depth": 0}

# --- MULTIPLAYER WEBSOCKET ---
rooms: Dict[str, List[WebSocket]] = {}

@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    await websocket.accept()
    if room_id not in rooms: rooms[room_id] = []
        
    if len(rooms[room_id]) >= 2:
        await websocket.send_json({"type": "error", "message": "Room is full!"})
        await websocket.close()
        return
        
    rooms[room_id].append(websocket)
    
    if len(rooms[room_id]) == 2:
        for ws in rooms[room_id]:
            await ws.send_json({"type": "start", "message": "Game Started! White to move."})
            
    try:
        while True:
            data = await websocket.receive_json()
            
            # Agar kisi player ne resign kar diya
            if data.get("type") == "resign":
                for ws in rooms[room_id]:
                    if ws != websocket:
                        await ws.send_json({"type": "disconnect", "message": "Opponent Resigned! You win 🏆"})
                continue

            for ws in rooms[room_id]:
                if ws != websocket:
                    await ws.send_json({"type": "move", "source": data["source"], "target": data["target"], "promotion": data.get("promotion", "q")})
    
    except WebSocketDisconnect:
        rooms[room_id].remove(websocket)
        for ws in rooms[room_id]:
            await ws.send_json({"type": "disconnect", "message": "Opponent disconnected. You win 🏆"})
        if len(rooms[room_id]) == 0:
            del rooms[room_id]
