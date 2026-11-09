-- AlterTable
ALTER TABLE "User" ADD COLUMN     "bannedAt" TIMESTAMP(3);

-- CreateTable
CREATE TABLE "QueryLog" (
    "id" SERIAL NOT NULL,
    "userId" TEXT,
    "agentType" TEXT NOT NULL,
    "timestamp" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "success" BOOLEAN NOT NULL,
    "tokenCount" INTEGER,
    "errorMessage" TEXT,
    "ipAddress" TEXT,

    CONSTRAINT "QueryLog_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "MeetingRoom" (
    "id" SERIAL NOT NULL,
    "name" TEXT NOT NULL,
    "createdBy" TEXT NOT NULL,
    "participants" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "agentType" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "MeetingRoom_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "MeetingMessage" (
    "id" SERIAL NOT NULL,
    "roomId" INTEGER NOT NULL,
    "userId" TEXT,
    "message" TEXT NOT NULL,
    "role" TEXT NOT NULL,
    "timestamp" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "MeetingMessage_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "InternalProject" (
    "id" SERIAL NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT,
    "createdBy" TEXT NOT NULL,
    "members" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "InternalProject_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "InternalChat" (
    "id" SERIAL NOT NULL,
    "userId" TEXT NOT NULL,
    "projectId" INTEGER,
    "agentType" TEXT NOT NULL,
    "message" TEXT NOT NULL,
    "role" TEXT NOT NULL,
    "timestamp" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "InternalChat_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "AdminAuditLog" (
    "id" SERIAL NOT NULL,
    "actorId" TEXT NOT NULL,
    "action" TEXT NOT NULL,
    "targetType" TEXT,
    "targetId" TEXT,
    "details" JSONB,
    "ipAddress" TEXT,
    "timestamp" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "AdminAuditLog_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "QueryLog_timestamp_idx" ON "QueryLog"("timestamp");

-- CreateIndex
CREATE INDEX "QueryLog_userId_timestamp_idx" ON "QueryLog"("userId", "timestamp");

-- CreateIndex
CREATE INDEX "QueryLog_agentType_timestamp_idx" ON "QueryLog"("agentType", "timestamp");

-- CreateIndex
CREATE INDEX "MeetingRoom_createdBy_idx" ON "MeetingRoom"("createdBy");

-- CreateIndex
CREATE INDEX "MeetingMessage_roomId_timestamp_idx" ON "MeetingMessage"("roomId", "timestamp");

-- CreateIndex
CREATE INDEX "InternalProject_createdBy_idx" ON "InternalProject"("createdBy");

-- CreateIndex
CREATE INDEX "InternalChat_userId_timestamp_idx" ON "InternalChat"("userId", "timestamp");

-- CreateIndex
CREATE INDEX "InternalChat_projectId_timestamp_idx" ON "InternalChat"("projectId", "timestamp");

-- CreateIndex
CREATE INDEX "AdminAuditLog_timestamp_idx" ON "AdminAuditLog"("timestamp");

-- CreateIndex
CREATE INDEX "AdminAuditLog_actorId_timestamp_idx" ON "AdminAuditLog"("actorId", "timestamp");

-- AddForeignKey
ALTER TABLE "MeetingMessage" ADD CONSTRAINT "MeetingMessage_roomId_fkey" FOREIGN KEY ("roomId") REFERENCES "MeetingRoom"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "InternalChat" ADD CONSTRAINT "InternalChat_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "InternalProject"("id") ON DELETE SET NULL ON UPDATE CASCADE;
