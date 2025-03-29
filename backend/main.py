from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import nba_api.stats.static.players as nbaP

app = FastAPI()

# ✅ Add CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow requests from any origin (change this for security)
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)
#Backend: Run uvicorn backend.main:app --reload
#Frontend: npm run serve

@app.get("/")
def read_root():
    listNBa = nbaP.get_active_players()
    

    return {"NBA": listNBa}

