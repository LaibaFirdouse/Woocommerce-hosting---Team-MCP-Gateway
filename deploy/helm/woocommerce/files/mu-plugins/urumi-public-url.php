<?php
/**
 * Plugin Name: Urumi WordPress public URL
 * Description: Forces home/siteurl from URUMI_WP_URL (stable, including local
 *              port-forward ports like :8090). A stable siteurl keeps WooCommerce
 *              COOKIEHASH / session cookies consistent so the cart persists.
 *              Also rewrites content/plugin asset URLs — WP_CONTENT_URL is defined
 *              before MU plugins load, so without this assets omit the local port
 *              and hit host Apache on :80.
 *              Optionally disables WooCommerce Coming Soon when URUMI_WP_COMING_SOON=0.
 */

$url = getenv( 'URUMI_WP_URL' );
if ( is_string( $url ) && $url !== '' ) {
	$urumi_public_url = untrailingslashit( $url );

	/**
	 * @return string
	 */
	function urumi_public_url() {
		static $cached = null;
		if ( $cached === null ) {
			$cached = untrailingslashit( (string) getenv( 'URUMI_WP_URL' ) );
		}
		return $cached;
	}

	/**
	 * Rewrite same-host URLs to the configured public origin (scheme+host[:port]).
	 *
	 * @param mixed $url URL to rewrite.
	 * @return mixed
	 */
	function urumi_align_url_to_public( $url ) {
		if ( ! is_string( $url ) || $url === '' ) {
			return $url;
		}
		$public = urumi_public_url();
		$pub    = wp_parse_url( $public );
		$parts  = wp_parse_url( $url );
		if ( ! is_array( $pub ) || ! is_array( $parts ) || empty( $pub['host'] ) || empty( $parts['host'] ) ) {
			return $url;
		}
		if ( strtolower( (string) $parts['host'] ) !== strtolower( (string) $pub['host'] ) ) {
			return $url;
		}
		$path  = $parts['path'] ?? '';
		$query = isset( $parts['query'] ) ? '?' . $parts['query'] : '';
		$frag  = isset( $parts['fragment'] ) ? '#' . $parts['fragment'] : '';
		return $public . $path . $query . $frag;
	}

	// Define COOKIEHASH from the configured public URL before WP defaults it
	// from a request-dependent siteurl (which breaks cart cookies across ports).
	if ( ! defined( 'COOKIEHASH' ) ) {
		define( 'COOKIEHASH', md5( $urumi_public_url ) );
	}

	add_filter( 'pre_option_home', static function () {
		return urumi_public_url();
	} );

	add_filter( 'pre_option_siteurl', static function () {
		return urumi_public_url();
	} );

	// WP_CONTENT_URL / plugin URLs are frozen before MU plugins; rewrite outputs.
	add_filter( 'content_url', 'urumi_align_url_to_public', 1 );
	add_filter( 'plugins_url', 'urumi_align_url_to_public', 1 );
	add_filter( 'script_loader_src', 'urumi_align_url_to_public', 1 );
	add_filter( 'style_loader_src', 'urumi_align_url_to_public', 1 );
	add_filter( 'theme_root_uri', 'urumi_align_url_to_public', 1 );
}

// Local demos: keep the storefront open for guest checkout (COD).
if ( getenv( 'URUMI_WP_COMING_SOON' ) === '0' ) {
	add_filter( 'pre_option_woocommerce_coming_soon', static function () {
		return 'no';
	} );
}
