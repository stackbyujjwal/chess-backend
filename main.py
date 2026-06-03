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
    sf = Stockfish(path="./stockfish-linux")
    sf.update_engine_parameters({"Hash": 1024, "Threads": 2}) 
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
            time_in_ms = pos.think_time * 1000
            best_move = stockfish.get_best_move_time(time_in_ms)
            
            evaluation = stockfish.get_evaluation()
            top_moves = stockfish.get_top_moves(1)
            
            score = round(evaluation["value"] / 100, 2) if evaluation["type"] == "cp" else f"Mate in {evaluation['value']}"
            pv = top_moves[0]['Move'] if top_moves else best_move
            
            return {"best_move": best_move, "score": score, "pv": pv, "depth": 20}
        except Exception as e:
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
