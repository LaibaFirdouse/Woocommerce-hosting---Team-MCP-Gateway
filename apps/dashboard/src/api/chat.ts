import { apiFetch } from "./client";
import type { ChatMessage, ChatResponse } from "./types";

export async function sendChat(
  messages: ChatMessage[],
): Promise<ChatResponse> {
  return apiFetch<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ messages }),
  });
}
