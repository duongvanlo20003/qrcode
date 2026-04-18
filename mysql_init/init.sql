CREATE DATABASE IF NOT EXISTS qr_scanner;
USE qr_scanner;

CREATE TABLE IF NOT EXISTS scan_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    qr_content TEXT NOT NULL,
    confidence FLOAT NOT NULL,
    timestamp DATETIME NOT NULL,
    method VARCHAR(50) NOT NULL
);
