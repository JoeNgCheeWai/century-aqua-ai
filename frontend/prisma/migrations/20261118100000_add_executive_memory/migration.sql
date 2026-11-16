-- CreateTable
-- The executives' episodic memory: a lesson, rule or insight worth keeping. The vector that finds it by meaning lives in Pinecone, under "vectorId".
-- This migration only ADDS one table and one unique index; it changes nothing that exists.
CREATE TABLE "ExecutiveMemory" (
    "id" TEXT NOT NULL,
    "vectorId" TEXT NOT NULL,
    "role" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "insight" TEXT NOT NULL,
    "context" TEXT,
    "sourceChatId" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ExecutiveMemory_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "ExecutiveMemory_vectorId_key" ON "ExecutiveMemory"("vectorId");
