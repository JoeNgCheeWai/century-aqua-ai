-- CreateEnum
CREATE TYPE "FetchRequester" AS ENUM ('CAO', 'CTO', 'CMO');

-- CreateEnum
CREATE TYPE "FetchPriority" AS ENUM ('HIGH', 'MED', 'LOW');

-- CreateEnum
CREATE TYPE "FetchStatus" AS ENUM ('PENDING', 'FETCHING', 'DONE', 'FAILED');

-- CreateTable
-- The COO's knowledge queue. This migration only ADDS two tables and three types; it changes nothing that exists.
CREATE TABLE "FetchRequest" (
    "id" SERIAL NOT NULL,
    "requester" "FetchRequester" NOT NULL,
    "query" TEXT NOT NULL,
    "priority" "FetchPriority" NOT NULL DEFAULT 'MED',
    "status" "FetchStatus" NOT NULL DEFAULT 'PENDING',
    "fetchedUrls" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "summaryText" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "processedAt" TIMESTAMP(3),

    CONSTRAINT "FetchRequest_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "IngestedSource" (
    "id" SERIAL NOT NULL,
    "urlKey" TEXT NOT NULL,
    "url" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "chunks" INTEGER NOT NULL DEFAULT 0,
    "fetchRequestId" INTEGER,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "IngestedSource_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "FetchRequest_status_priority_createdAt_idx" ON "FetchRequest"("status", "priority", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "IngestedSource_urlKey_key" ON "IngestedSource"("urlKey");

-- CreateIndex
CREATE INDEX "IngestedSource_fetchRequestId_idx" ON "IngestedSource"("fetchRequestId");
