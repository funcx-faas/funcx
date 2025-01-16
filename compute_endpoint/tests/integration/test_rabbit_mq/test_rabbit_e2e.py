import queue


def test_simple_roundtrip(
    flush_results,
    start_task_q_publisher,
    start_task_q_subscriber,
    start_result_q_subscriber,
    start_result_q_publisher,
    randomstring,
):
    task_q, result_q = queue.SimpleQueue(), queue.SimpleQueue()

    # Start the publishers *first* as that route creates the queues
    task_pub = start_task_q_publisher()
    result_pub = start_result_q_publisher()

    task_sub = start_task_q_subscriber(task_queue=task_q)
    result_sub = start_result_q_subscriber(result_q=result_q)

    message = f"Hello test_simple_roundtrip: {randomstring()}".encode()
    task_pub.publish(message)
    _, _, task_message = task_q.get(timeout=2)
    assert message == task_message

    result_pub.publish(task_message)
    _, result_message = result_q.get(timeout=2)

    task_sub._stop_event.set()
    result_sub.kill_event.set()

    _, expected = (result_pub.queue_info["test_routing_key"], message)
    assert result_message == expected
