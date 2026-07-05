use serde::{Deserialize, Serialize};
use std::io::{BufRead, Write};

/// Content-Length framed JSON-RPC 2.0 message (request).
#[derive(Debug, Serialize, Deserialize)]
pub struct RpcRequest {
    pub jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub protocol_version: Option<u32>,
    pub id: Option<u64>,
    pub method: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Value>,
}

/// Content-Length framed JSON-RPC 2.0 message (response or event).
///
/// When `id` is `Some`, this is a response to a prior request.
/// When `id` is `None` and `method` is present, this is an event notification.
#[derive(Debug, Serialize, Deserialize)]
pub struct RpcResponse {
    pub jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub protocol_version: Option<u32>,
    pub id: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<RpcError>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct RpcError {
    pub code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<serde_json::Value>,
    pub retryable: bool,
}

/// Event notification from the engine (no `id` field, has `method`).
#[derive(Debug, Serialize, Deserialize)]
pub struct RpcEvent {
    pub jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub protocol_version: Option<u32>,
    pub method: String,
    pub params: serde_json::Value,
}

/// Read one Content-Length framed message from a buffered reader.
///
/// The `reader` must be a persistent `BufRead` instance — this function
/// reads header lines then the exact body length. Calling it repeatedly
/// on the same `BufReader` correctly advances through the stream.
///
/// # Errors
///
/// Returns `UnexpectedEof` if the stream ends before the full message;
/// `InvalidData` if headers are malformed.
pub fn read_frame(reader: &mut impl BufRead) -> std::io::Result<Vec<u8>> {
    let mut content_length: Option<usize> = None;
    let mut line = String::new();

    // --- Read headers -------------------------------------------------------
    loop {
        line.clear();
        let bytes = reader.read_line(&mut line)?;
        if bytes == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "stream closed while reading headers",
            ));
        }

        let trimmed = line.trim_end_matches("\r\n").trim_end_matches('\n');
        if trimmed.is_empty() {
            // End of headers (blank line)
            break;
        }
        if let Some(len_str) = trimmed.strip_prefix("Content-Length: ") {
            content_length = Some(len_str.trim().parse::<usize>().map_err(|e| {
                std::io::Error::new(std::io::ErrorKind::InvalidData, e)
            })?);
        }
        // Other headers are ignored (Content-Type, etc.)
    }

    // --- Read body ----------------------------------------------------------
    let len = content_length.ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "missing Content-Length header",
        )
    })?;

    let mut body = vec![0u8; len];
    reader.read_exact(&mut body)?;
    Ok(body)
}

/// Write `data` as a Content-Length framed message to `writer`.
pub fn write_frame<W: Write>(writer: &mut W, data: &[u8]) -> std::io::Result<()> {
    let header = format!("Content-Length: {}\r\n\r\n", data.len());
    writer.write_all(header.as_bytes())?;
    writer.write_all(data)?;
    writer.flush()?;
    Ok(())
}
