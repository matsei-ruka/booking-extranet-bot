import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { Type } from "@sinclair/typebox";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { join } from "node:path";

const execFileAsync = promisify(execFile);

async function runCli(
  config: { pythonPath?: string; botDir: string },
  args: string[],
  timeoutMs = 120_000,
): Promise<{ stdout: string; stderr: string }> {
  const python = config.pythonPath || join(config.botDir, "venv", "bin", "python3");
  const cli = join(config.botDir, "cli.py");
  return execFileAsync(python, [cli, ...args], {
    timeout: timeoutMs,
    cwd: config.botDir,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });
}

function textResult(stdout: string, stderr: string) {
  return {
    content: [{ type: "text" as const, text: stdout || stderr }],
  };
}

function errorResult(err: any) {
  return {
    content: [
      {
        type: "text" as const,
        text: `Error: ${err.message}\n${err.stderr || ""}`,
      },
    ],
    isError: true,
  };
}

export default definePluginEntry({
  id: "booking-extranet",
  name: "Booking.com Extranet",
  description:
    "Manage Booking.com properties — reservations, messages, rates.",

  register(api) {
    const config = api.getConfig() as { pythonPath?: string; botDir: string };

    // ── list-properties ─────────────────────────────────────────
    api.registerTool({
      name: "booking_list_properties",
      description:
        "List all Booking.com properties with their hotel IDs and unread message counts.",
      parameters: Type.Object({}),
      async execute() {
        try {
          const { stdout, stderr } = await runCli(config, ["list-properties"]);
          return textResult(stdout, stderr);
        } catch (err: any) {
          return errorResult(err);
        }
      },
    });

    // ── download-reservations ───────────────────────────────────
    api.registerTool({
      name: "booking_download_reservations",
      description:
        "Download reservations for a date range. Returns JSON with reservation data or saves an Excel file.",
      parameters: Type.Object({
        start: Type.String({ description: "Start date YYYY-MM-DD" }),
        end: Type.String({ description: "End date YYYY-MM-DD" }),
        date_type: Type.Optional(
          Type.Union(
            [
              Type.Literal("arrival"),
              Type.Literal("departure"),
              Type.Literal("booking"),
            ],
            { description: "Date filter type (default: arrival)" },
          ),
        ),
        json: Type.Optional(
          Type.Boolean({
            description: "Return data as JSON instead of saving Excel file",
          }),
        ),
      }),
      async execute(_id, params) {
        try {
          const args = [
            "download-reservations",
            "--start",
            params.start,
            "--end",
            params.end,
          ];
          if (params.date_type) args.push("--date-type", params.date_type);
          if (params.json) args.push("--json");
          const { stdout, stderr } = await runCli(config, args, 180_000);
          return textResult(stdout, stderr);
        } catch (err: any) {
          return errorResult(err);
        }
      },
    });

    // ── list-messages ───────────────────────────────────────────
    api.registerTool({
      name: "booking_list_messages",
      description:
        "List guest messages for a property. Defaults to unanswered messages.",
      parameters: Type.Object({
        hotel_id: Type.String({ description: "Property hotel ID" }),
        filter: Type.Optional(
          Type.Union(
            [
              Type.Literal("unanswered"),
              Type.Literal("sent"),
              Type.Literal("all"),
            ],
            { description: "Message filter (default: unanswered)" },
          ),
        ),
      }),
      async execute(_id, params) {
        try {
          const args = ["list-messages", "--hotel-id", params.hotel_id];
          if (params.filter) args.push("--filter", params.filter);
          const { stdout, stderr } = await runCli(config, args);
          return textResult(stdout, stderr);
        } catch (err: any) {
          return errorResult(err);
        }
      },
    });

    // ── read-message ────────────────────────────────────────────
    api.registerTool({
      name: "booking_read_message",
      description:
        "Open and read a specific guest conversation, including reservation details.",
      parameters: Type.Object({
        hotel_id: Type.String({ description: "Property hotel ID" }),
        index: Type.Number({
          description: "Message index from list-messages (0-based)",
        }),
      }),
      async execute(_id, params) {
        try {
          const args = [
            "read-message",
            "--hotel-id",
            params.hotel_id,
            "--index",
            String(params.index),
          ];
          const { stdout, stderr } = await runCli(config, args);
          return textResult(stdout, stderr);
        } catch (err: any) {
          return errorResult(err);
        }
      },
    });

    // ── send-message ────────────────────────────────────────────
    api.registerTool({
      name: "booking_send_message",
      description:
        "Send a reply to a guest conversation. Use list-messages first to get the index.",
      parameters: Type.Object({
        hotel_id: Type.String({ description: "Property hotel ID" }),
        index: Type.Number({
          description: "Message index from list-messages (0-based)",
        }),
        message: Type.String({ description: "Reply text to send" }),
      }),
      async execute(_id, params) {
        try {
          const args = [
            "send-message",
            "--hotel-id",
            params.hotel_id,
            "--index",
            String(params.index),
            "--message",
            params.message,
          ];
          const { stdout, stderr } = await runCli(config, args);
          return textResult(stdout, stderr);
        } catch (err: any) {
          return errorResult(err);
        }
      },
    });

    // ── update-rates ────────────────────────────────────────────
    api.registerTool({
      name: "booking_update_rates",
      description:
        "Update room rates from the CSV pricing file for a specific property.",
      parameters: Type.Object({
        hotel_id: Type.Optional(
          Type.String({
            description: "Property hotel ID (default: from .env)",
          }),
        ),
      }),
      async execute(_id, params) {
        try {
          const args = ["update-rates"];
          if (params.hotel_id) args.push("--hotel-id", params.hotel_id);
          const { stdout, stderr } = await runCli(config, args, 300_000);
          return textResult(stdout, stderr);
        } catch (err: any) {
          return errorResult(err);
        }
      },
    });
  },
});
