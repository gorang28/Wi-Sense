import { logger } from "firebase-functions/v2";
import { initializeApp } from "firebase-admin/app";
import { FieldValue, getFirestore } from "firebase-admin/firestore";
import { getMessaging } from "firebase-admin/messaging";
import { onDocumentCreated, onDocumentUpdated } from "firebase-functions/v2/firestore";

initializeApp();

interface CaregiverToken {
  uid: string;
  token: string;
}

const STALE_TOKEN_ERROR_CODES = new Set([
  "messaging/registration-token-not-registered",
  "messaging/invalid-registration-token",
]);

async function caregiverTokens(houseId: string): Promise<CaregiverToken[]> {
  const db = getFirestore();
  const houseSnap = await db.collection("wisense_houses").doc(houseId).get();
  if (!houseSnap.exists) {
    logger.warn(`caregiverTokens: house ${houseId} does not exist`);
    return [];
  }

  const caregiverIds = (houseSnap.data()?.caregiverIds ?? []) as string[];
  logger.info(`caregiverTokens: house ${houseId} has caregiverIds=${JSON.stringify(caregiverIds)}`);
  if (caregiverIds.length === 0) return [];

  const userDocs = await db.getAll(
    ...caregiverIds.map((uid) => db.collection("wisense_users").doc(uid)),
  );
  const entries = userDocs.flatMap((doc) => {
    const tokens = (doc.data()?.fcmTokens ?? []) as string[];
    return tokens.filter(Boolean).map((token) => ({ uid: doc.id, token }));
  });
  logger.info(`caregiverTokens: resolved ${entries.length} token(s) for house ${houseId}`);
  return entries;
}

/**
 * Sends to every caregiver token for a house, then prunes any token FCM
 * reports as permanently dead (uninstalled app, cleared data, etc.) from
 * that user's doc — otherwise fcmTokens only ever grows via arrayUnion on
 * registration and every send accumulates more guaranteed failures forever.
 */
async function sendToCaregivers(
  houseId: string,
  data: Record<string, string>,
  highPriority: boolean,
): Promise<void> {
  const entries = await caregiverTokens(houseId);
  if (entries.length === 0) {
    logger.warn(`sendToCaregivers: no caregiver tokens for house ${houseId}, not sending push`);
    return;
  }

  const response = await getMessaging().sendEachForMulticast({
    tokens: entries.map((e) => e.token),
    data,
    ...(highPriority ? { android: { priority: "high" as const } } : {}),
  });
  logger.info(
    `sendToCaregivers: sent to ${entries.length} token(s) for house ${houseId}, ` +
      `successCount=${response.successCount} failureCount=${response.failureCount}`,
  );

  const staleTokensByUid = new Map<string, string[]>();
  response.responses.forEach((r, i) => {
    if (r.success) return;
    const { uid, token } = entries[i];
    logger.warn(`sendToCaregivers: token for ${uid} failed: ${r.error?.code} ${r.error?.message}`);
    if (r.error?.code && STALE_TOKEN_ERROR_CODES.has(r.error.code)) {
      staleTokensByUid.set(uid, [...(staleTokensByUid.get(uid) ?? []), token]);
    }
  });

  if (staleTokensByUid.size === 0) return;
  const db = getFirestore();
  await Promise.all(
    Array.from(staleTokensByUid.entries()).map(([uid, staleTokens]) =>
      db.collection("wisense_users").doc(uid).update({
        fcmTokens: FieldValue.arrayRemove(...staleTokens),
      }),
    ),
  );
  logger.info(
    `sendToCaregivers: pruned stale token(s) for ${staleTokensByUid.size} user(s) in house ${houseId}`,
  );
}

/**
 * Fires the instant a resident writes a new wisense_emergencies/{id} doc.
 * Data message (not a notification message) so the caregiver app has full
 * control over the incoming-call-style full-screen UI — required either
 * way to wake and route into the live stream even if the app was killed.
 */
export const onEmergencyCreated = onDocumentCreated(
  "wisense_emergencies/{emergencyId}",
  async (event) => {
    const emergency = event.data?.data();
    const houseId = emergency?.houseId as string | undefined;
    logger.info(`onEmergencyCreated: emergency=${event.params.emergencyId} houseId=${houseId}`);
    if (!houseId) {
      logger.warn(`onEmergencyCreated: emergency ${event.params.emergencyId} has no houseId, skipping`);
      return;
    }

    await sendToCaregivers(
      houseId,
      { type: "emergency_active", emergencyId: event.params.emergencyId, houseId },
      true,
    );
  },
);

/**
 * Fires when status transitions to "resolved" — pushes the all-clear and
 * cleans up the short-lived signaling subcollection now the call is over.
 */
export const onEmergencyResolved = onDocumentUpdated(
  "wisense_emergencies/{emergencyId}",
  async (event) => {
    const before = event.data?.before.data();
    const after = event.data?.after.data();
    if (!before || !after) return;
    if (before.status === after.status || after.status !== "resolved") return;

    const houseId = after.houseId as string | undefined;
    if (houseId) {
      await sendToCaregivers(
        houseId,
        { type: "emergency_resolved", emergencyId: event.params.emergencyId, houseId },
        false,
      );
    }

    const db = getFirestore();
    const signalingDocs = await db
      .collection("wisense_emergencies")
      .doc(event.params.emergencyId)
      .collection("signaling")
      .listDocuments();
    await Promise.all(signalingDocs.map((doc) => doc.delete()));
  },
);
