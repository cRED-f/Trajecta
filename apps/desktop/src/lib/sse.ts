export interface SSEMessage {
  event: string;
  data: string;
}

function parseFrame(frame: string): SSEMessage | null {
  if (!frame.trim()) {
    return null;
  }

  let event = "message";
  const data: string[] = [];

  for (const rawLine of frame.split("\n")) {
    const line = rawLine.replace(/\r$/, "");

    if (!line || line.startsWith(":")) {
      continue;
    }

    const colon = line.indexOf(":");

    const field =
      colon === -1
        ? line
        : line.slice(0, colon);

    let value =
      colon === -1
        ? ""
        : line.slice(colon + 1);

    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    if (field === "event") {
      event = value;
    } else if (field === "data") {
      data.push(value);
    }
  }

  if (data.length === 0) {
    return null;
  }

  return {
    event,
    data: data.join("\n"),
  };
}

export async function consumeSSE(
  response: Response,
  onMessage: (message: SSEMessage) => void | Promise<void>,
): Promise<void> {
  if (!response.body) {
    throw new Error("Streaming response has no body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();

    if (done) {
      buffer += decoder.decode();

      if (buffer.trim()) {
        const parsed = parseFrame(buffer);

        if (parsed) {
          await onMessage(parsed);
        }
      }

      break;
    }

    buffer += decoder.decode(value, {
      stream: true,
    });

    buffer = buffer.replace(/\r\n/g, "\n");

    let boundary = buffer.indexOf("\n\n");

    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);

      buffer = buffer.slice(boundary + 2);

      const parsed = parseFrame(frame);

      if (parsed) {
        await onMessage(parsed);
      }

      boundary = buffer.indexOf("\n\n");
    }
  }
}