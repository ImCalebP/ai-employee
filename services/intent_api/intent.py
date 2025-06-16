from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from openai import OpenAI, RateLimitError

from config.credentials import settings
from common.teams_client import post_chat


# ---------- OpenAI wrapper ---------------------------------------------------
openai = OpenAI(api_key=settings.OPENAI_API_KEY.get_secret_value())

_SYSTEM = (
    "You are Alexander, a concise but helpful assistant. "
    "Answer in the user's language."
)


def gpt_reply(user_msg: str) -> str:
    try:
        rsp = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.7,
        )
        return rsp.choices[0].message.content.strip()
    except RateLimitError as e:
        raise HTTPException(429, f"OpenAI rate-limit: {e}")


# ---------- FastAPI service --------------------------------------------------
app = FastAPI(title="intent-api MVP (GPT reply)")


class InMsg(BaseModel):
    chat_id: str = Field(..., description="Teams chat GUID")
    text:    str = Field(..., description="User message")


class OutMsg(BaseModel):
    reply: str


@app.post("/answer", response_model=OutMsg)
async def answer(msg: InMsg):
    reply = gpt_reply(msg.text)                 # ask GPT-4o
    await post_chat(msg.chat_id, reply)         # echo back into Teams
    return OutMsg(reply=reply)
