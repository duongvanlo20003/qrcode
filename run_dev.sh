#!/bin/bash

# Hàm hiển thị hướng dẫn
usage() {
    echo "Sử dụng: $0 [start|stop|restart]"
    echo "  start   : Khởi động hệ thống (build & up)"
    echo "  stop    : Dừng và xóa các container"
    echo "  restart : Khởi động lại toàn bộ hệ thống (mặc định)"
    exit 1
}

# Nếu không có tham số, mặc định là restart
ACTION=${1:-restart}

case "$ACTION" in
    start)
        echo "🚀 Đang khởi động hệ thống QR Code Processing..."
        docker compose build
        docker compose up -d
        ;;
    stop)
        echo "🛑 Đang dừng hệ thống..."
        docker compose down
        ;;
    restart)
        echo "♻️  Đang khởi động lại toàn bộ hệ thống..."
        docker compose down
        docker compose build
        docker compose up -d
        ;;
    *)
        usage
        ;;
esac

if [ "$ACTION" != "stop" ]; then
    echo "✅ Hệ thống đã sẵn sàng!"
    echo "👉 Truy cập Frontend: http://localhost:3000"
    echo "👉 Truy cập Backend: http://localhost:8000"
    echo "👉 Truy cập MySQL Adminer: http://localhost:8080"
    echo "    -> DB: MySQL | Server: mysqldb | User: root | Pass: root"
fi
