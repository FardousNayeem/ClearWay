export type ApiErrorBody = {
  error?: {
    code: string;
    message: string;
    details: Record<string, string> | null;
    request_id?: string;
  };
};

/** Every API failure arrives in one envelope, so the UI handles one shape. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: Record<string, string>;

  constructor(
    status: number,
    code: string,
    message: string,
    fields: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }

  static from(status: number, body: ApiErrorBody | null): ApiError {
    const error = body?.error;
    if (!error) {
      return new ApiError(status, "network_error", "Clearway is not responding.");
    }
    return new ApiError(status, error.code, error.message, error.details ?? {});
  }

  get friendly(): string {
    switch (this.code) {
      case "no_data":
        return "No monitoring station or model output covers that location yet.";
      case "upstream_unavailable":
        return "An open data provider is not responding. This usually clears within a few minutes.";
      case "model_unavailable":
        return "No model has been trained yet, so only the raw physics forecast is available.";
      case "validation_error":
        return "That location does not look right.";
      case "network_error":
        return "Clearway is not responding. Check the API is running.";
      default:
        return this.message;
    }
  }
}

export const isApiError = (error: unknown): error is ApiError =>
  error instanceof ApiError;
