-- Neon 雲端資料庫初始化
-- 啟用 pgvector 擴充
-- 注意：向量欄位維度 = 1024（對應 BGE-M3 embedding 模型）
CREATE EXTENSION IF NOT EXISTS vector;