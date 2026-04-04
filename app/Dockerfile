FROM golang:1.22-alpine AS builder

WORKDIR /build
COPY main.go .
RUN go mod init order-service && \
    go build -o order-service .

FROM alpine:3.19
RUN apk --no-cache add ca-certificates
COPY --from=builder /build/order-service /usr/local/bin/order-service

EXPOSE 8080
CMD ["order-service"]
