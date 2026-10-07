"""logs.configure: one rotating file plus stderr, safe to call twice."""

import logging
import logging.handlers

import pytest

import logs


@pytest.fixture(autouse=True)
def _clean_logger():
    yield
    logger = logging.getLogger("clefline")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.NOTSET)


def test_configure_writes_to_a_rotating_file(tmp_path):
    logs.configure(directory=tmp_path, level="INFO")
    logging.getLogger("clefline.pipeline").info("job abc started")
    for handler in logging.getLogger("clefline").handlers:
        handler.flush()

    text = (tmp_path / "clefline.log").read_text()
    assert "job abc started" in text
    assert "clefline.pipeline" in text and "INFO" in text


def test_the_file_handler_rotates():
    handlers = logs.configure(directory=None, level="INFO", file=False)
    assert handlers  # stderr only when no file is wanted


def test_configure_uses_a_size_limited_rotating_handler(tmp_path):
    logs.configure(directory=tmp_path, level="INFO")
    rotating = [h for h in logging.getLogger("clefline").handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(rotating) == 1
    assert rotating[0].maxBytes == 5 * 1024 * 1024 and rotating[0].backupCount == 3


def test_calling_configure_twice_does_not_duplicate_handlers_or_lines(tmp_path):
    logs.configure(directory=tmp_path, level="INFO")
    logs.configure(directory=tmp_path, level="INFO")
    logging.getLogger("clefline.pipeline").warning("only once")
    for handler in logging.getLogger("clefline").handlers:
        handler.flush()

    assert (tmp_path / "clefline.log").read_text().count("only once") == 1


def test_the_level_filters_quieter_messages(tmp_path):
    logs.configure(directory=tmp_path, level="WARNING")
    logging.getLogger("clefline.pipeline").info("chatty")
    logging.getLogger("clefline.pipeline").warning("important")
    for handler in logging.getLogger("clefline").handlers:
        handler.flush()

    text = (tmp_path / "clefline.log").read_text()
    assert "important" in text and "chatty" not in text


def test_an_unwritable_log_directory_falls_back_to_stderr_without_crashing(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    handlers = logs.configure(directory=blocker / "logs", level="INFO")
    assert handlers  # the server must still start; stderr keeps working
    logging.getLogger("clefline.pipeline").info("still logs somewhere")
