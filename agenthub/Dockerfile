FROM golang:1.26-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /agenthub-server ./cmd/agenthub-server
RUN CGO_ENABLED=0 go build -o /ah ./cmd/ah

FROM alpine:latest
RUN apk add --no-cache git
COPY --from=builder /agenthub-server /usr/local/bin/agenthub-server
COPY --from=builder /ah /usr/local/bin/ah
EXPOSE 8080
CMD ["/usr/local/bin/agenthub-server"]
