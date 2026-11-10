-- AlterTable
ALTER TABLE "AgentAuditLog" ADD COLUMN     "humanActionAt" TIMESTAMP(3),
ADD COLUMN     "humanActionBy" TEXT;
