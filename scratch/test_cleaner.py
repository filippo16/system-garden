import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "oracle_vps")))

from cleaner import MessageCleaner, DELETIONS_FILE

deleted_messages = []

class MockBot:
    async def delete_message(self, chat_id, message_id):
        deleted_messages.append((chat_id, message_id))
        return True

async def test_cleaner_logic():
    # Clean up test file if exists
    if os.path.exists(DELETIONS_FILE):
        try: os.remove(DELETIONS_FILE)
        except Exception: pass

    mock_bot = MockBot()
    cleaner = MessageCleaner(bot=mock_bot)

    # 1. Test schedule deletion
    cleaner.schedule_deletion(chat_id=123, message_id=101, delay_seconds=1)
    cleaner.schedule_deletion(chat_id=123, message_id=102, delay_seconds=86400)

    assert len(cleaner._queue) == 2, f"Expected 2 items in queue, got {len(cleaner._queue)}"
    assert os.path.exists(DELETIONS_FILE), "deletions file was not created"

    # 2. Test reload from file
    cleaner2 = MessageCleaner(bot=mock_bot)
    assert len(cleaner2._queue) == 2, f"Expected 2 reloaded items, got {len(cleaner2._queue)}"

    # 3. Fast-forward expiration
    cleaner._queue[0]["delete_at"] = time.time() - 10 # expired

    # Start loop briefly
    await cleaner.start()
    await asyncio.sleep(0.5) # Let iteration run
    await cleaner.stop()

    assert (123, 101) in deleted_messages, f"Message 101 was not deleted: {deleted_messages}"
    assert len(cleaner._queue) == 1, f"Expected 1 item remaining in queue, got {len(cleaner._queue)}"
    assert cleaner._queue[0]["message_id"] == 102, "Wrong item remaining in queue"

    # Cleanup
    if os.path.exists(DELETIONS_FILE):
        try: os.remove(DELETIONS_FILE)
        except Exception: pass

    print("ALL CLEANER TESTS PASSED SUCCESSFULLY! [OK]")

if __name__ == "__main__":
    asyncio.run(test_cleaner_logic())
