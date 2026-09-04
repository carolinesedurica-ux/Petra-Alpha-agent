import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/index.css";
import App from "@/App";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("Petra Terminal Uncaught Error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: "100vh",
          backgroundColor: "#0a0e17",
          color: "#e2e8f0",
          fontFamily: "JetBrains Mono, monospace",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "24px",
          textAlign: "center"
        }}>
          <div style={{
            border: "1px solid rgba(239, 68, 68, 0.4)",
            backgroundColor: "rgba(239, 68, 68, 0.08)",
            borderRadius: "8px",
            padding: "24px 32px",
            maxWidth: "600px"
          }}>
            <h1 style={{ color: "#ef4444", fontSize: "18px", fontWeight: "bold", marginBottom: "12px" }}>
              Terminal Runtime Exception
            </h1>
            <p style={{ fontSize: "12px", color: "#94a3b8", marginBottom: "16px" }}>
              {this.state.error?.message || "An unexpected error occurred during rendering."}
            </p>
            <button
              onClick={() => window.location.reload()}
              style={{
                backgroundColor: "#00F0B5",
                color: "#0a0e17",
                border: "none",
                borderRadius: "4px",
                padding: "8px 16px",
                fontSize: "12px",
                fontWeight: "bold",
                cursor: "pointer"
              }}
            >
              Reload Terminal
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
