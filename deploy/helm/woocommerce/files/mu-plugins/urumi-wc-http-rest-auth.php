<?php
/**
 * Plugin Name: Urumi WooCommerce HTTP REST Auth (local dev)
 * Description: Treats WooCommerce Admin REST (wc/v*) as HTTPS so API key auth works over plain HTTP. Gated by URUMI_WC_ALLOW_HTTP_REST_AUTH=1.
 *
 * WooCommerce only runs consumer key/secret authentication when is_ssl() is true; on HTTP it requires OAuth 1.0a.
 * Local Kind clusters expose WordPress on HTTP, so MCP clients would otherwise always get 401.
 *
 * Intentionally excludes Store API (/wc/store/) so checkout thank-you URLs stay on the public http:// host.
 */

if ( getenv( 'URUMI_WC_ALLOW_HTTP_REST_AUTH' ) !== '1' ) {
	return;
}

/**
 * @return bool
 */
function urumi_wc_is_admin_rest_request() {
	if ( empty( $_SERVER['REQUEST_URI'] ) ) {
		return false;
	}

	$uri = wp_unslash( $_SERVER['REQUEST_URI'] );
	if ( ! is_string( $uri ) ) {
		return false;
	}

	// Store API uses nonce/cart-token auth — do not force SSL (breaks checkout redirects).
	if ( str_contains( $uri, '/wc/store' ) || str_contains( $uri, 'rest_route=/wc/store' ) ) {
		return false;
	}

	$rest_prefix = rest_get_url_prefix();
	return (
		str_contains( $uri, '/' . $rest_prefix . '/wc/' )
		|| str_contains( $uri, 'rest_route=/wc/' )
		|| str_contains( $uri, 'rest_route=%2Fwc%2F' )
	);
}

/**
 * @param int|false $user_id Current user ID.
 * @return int|false
 */
function urumi_wc_maybe_enable_ssl_for_rest_auth( $user_id ) {
	if ( is_ssl() || ! urumi_wc_is_admin_rest_request() ) {
		return $user_id;
	}

	$GLOBALS['urumi_wc_https_previous'] = array_key_exists( 'HTTPS', $_SERVER ) ? $_SERVER['HTTPS'] : null;
	$_SERVER['HTTPS']                   = 'on';

	return $user_id;
}

/**
 * Restore previous HTTPS flag after REST authentication finishes.
 *
 * @param mixed $result Auth result.
 * @return mixed
 */
function urumi_wc_restore_https_after_rest_auth( $result ) {
	if ( ! array_key_exists( 'urumi_wc_https_previous', $GLOBALS ) ) {
		return $result;
	}

	$previous = $GLOBALS['urumi_wc_https_previous'];
	unset( $GLOBALS['urumi_wc_https_previous'] );

	if ( $previous === null ) {
		unset( $_SERVER['HTTPS'] );
	} else {
		$_SERVER['HTTPS'] = $previous;
	}

	return $result;
}

add_filter( 'determine_current_user', 'urumi_wc_maybe_enable_ssl_for_rest_auth', 14 );
add_filter( 'rest_authentication_errors', 'urumi_wc_restore_https_after_rest_auth', 999 );
