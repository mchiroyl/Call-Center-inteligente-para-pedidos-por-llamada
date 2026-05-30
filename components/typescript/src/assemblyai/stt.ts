import WebSocket from "ws";
import { writableIterator } from "../utils";
import type { AssemblyAISTTMessage } from "./api-types";
import type { VoiceAgentEvent } from "../types";

function envBool(name: string): boolean | undefined {
  const v = process.env[name];
  if (v === undefined || v === "") return undefined;
  const s = v.toLowerCase();
  if (["1", "true", "yes", "on"].includes(s)) return true;
  if (["0", "false", "no", "off"].includes(s)) return false;
  return undefined;
}

/** Universal Streaming `language` query param: only `en` | `multi` per API. */
function normalizeStreamingLanguage(raw: string | undefined): "en" | "multi" | "" {
  if (!raw) return "";
  const s = raw.trim().toLowerCase();
  if (s === "en" || s === "english") return "en";
  if (s === "multi" || s === "multilingual" || s === "es" || s === "spanish") {
    return "multi";
  }
  return "";
}

interface AssemblyAISTTOptions {
  apiKey?: string;
  sampleRate?: number;
  formatTurns?: boolean;
  /** Required by v3 streaming; defaults to universal-streaming-multilingual */
  speechModel?: string;
  /** Adds language_detection=true when true (default: on for multilingual model) */
  languageDetection?: boolean;
  /** Streaming API `language`: `en` or `multi` (Spanish-oriented sessions use `multi`) */
  streamingLanguage?: string;
}

export class AssemblyAISTT {
  apiKey: string;
  sampleRate: number;
  formatTurns: boolean;
  speechModel: string;
  languageDetection: boolean;
  streamingLanguage: "en" | "multi" | "";

  protected _bufferIterator = writableIterator<VoiceAgentEvent.STTEvent>();
  protected _connectionPromise: Promise<WebSocket> | null = null;
  protected get _connection(): Promise<WebSocket> {
    if (this._connectionPromise) {
      return this._connectionPromise;
    }

    this._connectionPromise = new Promise((resolve, reject) => {
      const params = new URLSearchParams({
        sample_rate: this.sampleRate.toString(),
        format_turns: this.formatTurns.toString().toLowerCase(),
        speech_model: this.speechModel,
      });
      if (this.languageDetection) {
        params.set("language_detection", "true");
      }
      if (this.streamingLanguage) {
        params.set("language", this.streamingLanguage);
      }

      const url = `wss://streaming.assemblyai.com/v3/ws?${params.toString()}`;
      const ws = new WebSocket(url, {
        headers: { Authorization: this.apiKey },
      });

      ws.on("open", () => {
        resolve(ws);
      });

      ws.on("message", (data: WebSocket.RawData) => {
        try {
          const message: AssemblyAISTTMessage = JSON.parse(data.toString());
          if (message.type === "Begin") {
            // no-op
          } else if (message.type === "Turn") {
            const transcript = (message.transcript ?? "").trim();
            const utterance = (message.utterance ?? "").trim();
            const endOfTurn = message.end_of_turn;
            if (endOfTurn) {
              const finalText = utterance || transcript;
              if (finalText) {
                this._bufferIterator.push({
                  type: "stt_output",
                  transcript: finalText,
                  ts: Date.now(),
                });
              }
            } else if (transcript) {
              this._bufferIterator.push({
                type: "stt_chunk",
                transcript,
                ts: Date.now(),
              });
            }
          } else if (message.type === "Termination") {
            // no-op
          } else if (message.type === "Error") {
            throw new Error(message.error);
          }
        } catch (error) {
          // TODO: better catch json parsing error
          console.error(error);
        }
      });

      ws.on("error", (error) => {
        this._bufferIterator.cancel();
        reject(error);
      });

      ws.on("close", () => {
        this._connectionPromise = null;
      });
    });

    return this._connectionPromise;
  }

  constructor(options: AssemblyAISTTOptions) {
    this.apiKey = options.apiKey || process.env.ASSEMBLYAI_API_KEY || "";
    this.sampleRate = options.sampleRate || 16000;
    this.formatTurns = options.formatTurns || true;
    this.speechModel =
      options.speechModel ||
      process.env.ASSEMBLYAI_SPEECH_MODEL ||
      "universal-streaming-multilingual";

    const ld = envBool("ASSEMBLYAI_LANGUAGE_DETECTION");
    if (options.languageDetection !== undefined) {
      this.languageDetection = options.languageDetection;
    } else if (ld !== undefined) {
      this.languageDetection = ld;
    } else {
      this.languageDetection = this.speechModel
        .toLowerCase()
        .includes("multilingual");
    }

    const langRaw =
      options.streamingLanguage ?? process.env.ASSEMBLYAI_STREAMING_LANGUAGE;
    let lang = normalizeStreamingLanguage(langRaw);
    if (this.speechModel.toLowerCase().includes("multilingual")) {
      if (!lang) lang = "multi";
      this.streamingLanguage = lang;
    } else {
      this.streamingLanguage = "";
    }

    if (!this.apiKey) {
      throw new Error("AssemblyAI API key is required");
    }
  }

  async sendAudio(buffer: Uint8Array): Promise<void> {
    const conn = await this._connection;
    conn.send(buffer);
  }

  async *receiveEvents(): AsyncGenerator<VoiceAgentEvent.STTEvent> {
    yield* this._bufferIterator;
  }

  async close(): Promise<void> {
    if (this._connectionPromise) {
      const ws = await this._connectionPromise;
      ws.close();
    }
  }
}
